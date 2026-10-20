"""Preserve regions: keep-part naming, regions.json, and surface-in-keep measurement (plan Phases 5-6).

A region is one ROLE + label, e.g. FIX_1. It owns one or more keep parts:
  - from the Preserve region feature: KEEP_<ROLE>_<label>, or KEEP_<ROLE>_<label>_<k> per connected face
    group, each carrying a PreserveRegion attribute (role, label, t, m, face count, area);
  - hand-modelled: KEEP_<ROLE>_<label>, with no attribute; the wall thickness comes from the part YAML.

For a keep solid K and tolerance eps, the exclusion faces S are the parts of SPACE's surface strictly inside K
(signed distance > eps); the preserved surface P is the part inside or on K (>= -eps).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import trimesh

ROLES = ("FIX", "LOAD", "PASSIVE")
KEEP_NAME = re.compile(r"^KEEP_(FIX|LOAD|PASSIVE)_([A-Z0-9_]+)$")


@dataclass
class Keep:
    part: str                       # Onshape part name, KEEP_...
    part_id: str = ""
    face_count: int | None = None   # from the attribute; None for hand-modelled keeps
    area_mm2: float | None = None


@dataclass
class Region:
    name: str                       # <ROLE>_<label>
    role: str
    label: str
    wall_thickness: float           # mm, t (also the Exclusion Thickness)
    void_margin: float | None       # mm, m; None for hand-modelled keeps
    source: str                     # "attribute" or "part_yaml"
    keeps: list[Keep] = field(default_factory=list)

    @property
    def face_count(self) -> int | None:
        fc = [k.face_count for k in self.keeps]
        return None if None in fc else sum(fc)

    @property
    def area_mm2(self) -> float | None:
        a = [k.area_mm2 for k in self.keeps]
        return None if None in a else float(sum(a))


def parse_keep_name(name: str) -> tuple[str, str] | None:
    """(role, label-with-any-group-suffix) for a well-formed KEEP_ name, else None."""
    m = KEEP_NAME.match(name)
    return (m.group(1), m.group(2)) if m else None


def build_regions(keep_parts: dict[str, str], attributes: dict[str, dict], hand_wall: float | None) -> list[Region]:
    """Group keep parts into regions.

    keep_parts: part name -> part id, for every part whose name starts with KEEP_.
    attributes: part name -> PreserveRegion attribute (feature-made keeps only).
    hand_wall:  wall thickness for hand-modelled keeps (part YAML keeps.wall_thickness), or None.
    Raises ValueError on malformed names, a hand-modelled keep without a wall thickness, or a region whose keeps
    come from inconsistent features.
    """
    regions: dict[str, Region] = {}
    group_counts: dict[str, int] = {}
    for part, pid in sorted(keep_parts.items()):
        parsed = parse_keep_name(part)
        if parsed is None:
            raise ValueError(f"malformed keep name {part!r}: expected KEEP_<FIX|LOAD|PASSIVE>_<A-Z0-9_>")
        att = attributes.get(part)
        if att is not None:
            role, label = att["role"], att["label"]
            expected = f"KEEP_{role}_{label}" + (f"_{int(att['group'])}" if int(att["groupCount"]) > 1 else "")
            if part != expected:
                raise ValueError(f"keep {part!r} was renamed; its attribute says {expected!r}")
            r = regions.setdefault(f"{role}_{label}", Region(
                f"{role}_{label}", role, label, float(att["wallThicknessMm"]), float(att["voidMarginMm"]), "attribute"))
            if r.source != "attribute" or (r.wall_thickness, r.void_margin) != (
                    float(att["wallThicknessMm"]), float(att["voidMarginMm"])):
                raise ValueError(f"region {r.name} mixes keeps from different features or hand-modelled parts")
            r.keeps.append(Keep(part, pid, int(att["faceCount"]), float(att["areaMm2"])))
            group_counts[r.name] = int(att["groupCount"])
        else:
            role, label = parsed
            if hand_wall is None:
                raise ValueError(f"hand-modelled keep {part!r} has no attribute and the part YAML has no "
                                 "keeps.wall_thickness")
            name = f"{role}_{label}"
            if name in regions:
                raise ValueError(f"region {name} mixes keeps from different features or hand-modelled parts")
            regions[name] = Region(name, role, label, float(hand_wall), None, "part_yaml", [Keep(part, pid)])
    for r in regions.values():
        n = group_counts.get(r.name)
        if n is not None and n != len(r.keeps):
            raise ValueError(f"region {r.name}: attribute says {n} keep bodies, found {len(r.keeps)}")
    return list(regions.values())


def save_regions(path: Path, regions: list[Region], extra: dict | None = None) -> None:
    out = dict(extra or {})
    out["regions"] = [{**{k: v for k, v in asdict(r).items()}, "face_count": r.face_count, "area_mm2": r.area_mm2}
                      for r in regions]
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")


def load_regions(path: Path) -> list[Region]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for d in data["regions"]:
        keeps = [Keep(**k) for k in d["keeps"]]
        out.append(Region(d["name"], d["role"], d["label"], d["wall_thickness"], d["void_margin"], d["source"], keeps))
    return out


# Interior quadrature points (barycentric) per triangle, equal weights: the centroid and three points towards the
# corners. Onshape tessellates each CAD face separately, so a region's boundary (a union of whole faces, or a split
# face) lies on triangle edges and nearly every triangle is wholly in or out; the points only matter for faces
# that K cuts through.
_BARY = np.array([[1 / 3, 1 / 3, 1 / 3], [2 / 3, 1 / 6, 1 / 6], [1 / 6, 2 / 3, 1 / 6], [1 / 6, 1 / 6, 2 / 3]])


def surface_in_keep(space: trimesh.Trimesh, keep: trimesh.Trimesh, eps: float) -> float:
    """Area of S: SPACE's surface strictly inside keep (signed distance > eps), by per-triangle quadrature."""
    lo, hi = keep.bounds
    tri = space.triangles
    cand = np.all(tri.max(axis=1) >= lo - eps, axis=1) & np.all(tri.min(axis=1) <= hi + eps, axis=1)
    if not cand.any():
        return 0.0
    tri = tri[cand]
    areas = space.area_faces[cand]
    normals = space.face_normals[cand]
    pts = np.einsum("qk,nkd->nqd", _BARY, tri).reshape(-1, 3)
    nrm = np.repeat(normals, len(_BARY), axis=0)
    # Cheap necessary condition for sd > eps: the point and both of its normal offsets are inside. It rejects the
    # points lying on K's boundary (SPACE faces flush with a keep face), on which the exact test is slow.
    ok = keep.contains(pts)
    for sign in (1.0, -1.0):
        idx = np.flatnonzero(ok)
        ok[idx] = keep.contains(pts[idx] + sign * 1.5 * eps * nrm[idx])
    idx = np.flatnonzero(ok)
    if len(idx):
        ok[idx] = trimesh.proximity.signed_distance(keep, pts[idx]) > eps   # positive inside
    frac = ok.reshape(-1, len(_BARY)).mean(axis=1)
    return float((areas * frac).sum())
