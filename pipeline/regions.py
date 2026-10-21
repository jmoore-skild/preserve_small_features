"""Preserve regions: keep-part naming, regions.json, and surface-in-keep measurement (plan Phases 5-6).

A region is one ROLE + label, e.g. FIX_1. It owns one or more keep parts:
  - from the Preserve region feature: KEEP_<ROLE>_<label>, or KEEP_<ROLE>_<label>_<k> per connected face
    group, each carrying a PreserveRegion attribute (role, label, t, m, face count, area);
  - hand-modelled: KEEP_<ROLE>_<label>, with no attribute; the wall thickness comes from the part YAML.

For a keep solid K and tolerance eps, the exclusion faces S are the parts of SPACE's surface strictly inside K
(signed distance > eps); the preserved surface P is the part inside or on K (>= -eps) with K's material behind it
(the point 2 eps into the material is inside K). The second condition keeps flush faces such as the beam's seats
and drops faces that only touch K's void margin, like a counterbore wall above a marked step.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import trimesh

from pipeline import geom

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


def _near_keep(space: trimesh.Trimesh, keep: trimesh.Trimesh, margin: float) -> np.ndarray:
    """Indices of SPACE triangles whose bounding box comes within `margin` of the keep's."""
    lo, hi = keep.bounds
    tri = space.triangles
    return np.flatnonzero(np.all(tri.max(axis=1) >= lo - margin, axis=1)
                          & np.all(tri.min(axis=1) <= hi + margin, axis=1))


def s_face_fraction(space: trimesh.Trimesh, keep: trimesh.Trimesh, eps: float) -> np.ndarray:
    """Per SPACE triangle, the fraction of its quadrature points strictly inside keep (signed distance > eps)."""
    frac = np.zeros(len(space.faces))
    cand = _near_keep(space, keep, eps)
    if len(cand):
        pts = np.einsum("qk,nkd->nqd", _BARY, space.triangles[cand]).reshape(-1, 3)
        frac[cand] = (geom.signed_distance(keep, pts) > eps).reshape(-1, len(_BARY)).mean(axis=1)
    return frac


def surface_in_keep(space: trimesh.Trimesh, keep: trimesh.Trimesh, eps: float) -> float:
    """Area of S: SPACE's surface strictly inside keep (signed distance > eps), by per-triangle quadrature."""
    return float((space.area_faces * s_face_fraction(space, keep, eps)).sum())


def s_triangles(space: trimesh.Trimesh, keep: trimesh.Trimesh, eps: float) -> trimesh.Trimesh:
    """The SPACE triangles that make up S (most of their quadrature points strictly inside keep)."""
    sel = np.flatnonzero(s_face_fraction(space, keep, eps) > 0.5)
    if not len(sel):
        raise ValueError("keep holds none of SPACE's surface")
    return space.submesh([sel], append=True)


def preserved_samples(space: trimesh.Trimesh, keep: trimesh.Trimesh, eps: float, spacing: float) -> np.ndarray:
    """Points on P, about one per spacing²: SPACE's surface inside or on keep (signed distance >= -eps) whose
    material side is inside keep (the point 2 eps along the inward normal has signed distance > 0)."""
    cand = _near_keep(space, keep, eps)
    if not len(cand):
        return np.empty((0, 3))
    pts, fi = geom.sample_surface(space, spacing, cand)
    lo, hi = keep.bounds            # long sliver triangles reach far beyond the keep
    sel = np.all(pts >= lo - eps, axis=1) & np.all(pts <= hi + eps, axis=1)
    pts, fi = pts[sel], fi[sel]
    sel = geom.signed_distance(keep, pts) >= -eps
    pts, fi = pts[sel], fi[sel]
    behind = pts - 2 * eps * space.face_normals[fi]
    return pts[geom.signed_distance(keep, behind) > 0]


# --------------------------------------------------------------------------- a part's geometry

@dataclass
class PartGeometry:
    """SPACE, the regions and their keep solids, all in the CAD frame (mm)."""
    space: trimesh.Trimesh
    regions: list[Region]
    keep_parts: dict[str, trimesh.Trimesh]      # KEEP_* part name -> solid
    keep: trimesh.Trimesh                       # union of every keep
    _region_keeps: dict = field(default_factory=dict, repr=False)

    def region(self, name: str) -> Region:
        return next(r for r in self.regions if r.name == name)

    def of_role(self, role: str) -> list[Region]:
        return [r for r in self.regions if r.role == role]

    def region_keep(self, name: str) -> trimesh.Trimesh:
        """K for a region: the union of its keep parts."""
        if name not in self._region_keeps:
            parts = [self.keep_parts[k.part] for k in self.region(name).keeps]
            self._region_keeps[name] = parts[0] if len(parts) == 1 else geom.to_trimesh(
                geom.union_all(geom.to_manifold(p) for p in parts))
        return self._region_keeps[name]


def load_part_geometry(gdir: Path) -> PartGeometry:
    """geometry/<part>/ after a passing geometry_check: space.stl, keep.stl, keep_parts/, regions.json."""
    gdir = Path(gdir)
    if not (gdir / "keep.stl").exists():
        raise FileNotFoundError(f"{gdir / 'keep.stl'} not found: run geometry_check on the part first")
    regions = load_regions(gdir / "regions.json")
    parts = {k.part: geom.load_mesh(gdir / "keep_parts" / f"{k.part}.stl") for r in regions for k in r.keeps}
    return PartGeometry(geom.load_mesh(gdir / "space.stl"), regions, parts, geom.load_mesh(gdir / "keep.stl"))


def standin_geometry(cfg: dict, wall_thickness: float = 3.75) -> PartGeometry:
    """The procedural beam (pipeline.geom) with hand-modelled keeps FIX_1..4, LOAD_1..2, PASSIVE_1..2."""
    parts = {n: geom.to_trimesh(m) for n, m in geom.make_keep_parts(cfg).items()}
    regions = build_regions({n: "" for n in parts}, {}, wall_thickness)
    keep = geom.to_trimesh(geom.union_all(geom.to_manifold(m) for m in parts.values()))
    return PartGeometry(geom.to_trimesh(geom.make_space(cfg)), regions, parts, keep)


def transformed(geo: PartGeometry, matrix: np.ndarray) -> PartGeometry:
    """A copy with every mesh moved by a 4x4 rigid transform."""
    def mv(m):
        return m.copy().apply_transform(matrix)
    return PartGeometry(mv(geo.space), geo.regions, {n: mv(m) for n, m in geo.keep_parts.items()}, mv(geo.keep))


# --------------------------------------------------------------------------- boundary conditions

def resolve_bc_regions(entries: list[dict], role: str, regions: list[Region]) -> tuple[list[list[str]], list[str]]:
    """Region names each supports/loads entry of the part YAML applies to (`all` = every region of the role), and
    any problems (unknown region, wrong role, an entry with no regions)."""
    of_role = [r.name for r in regions if r.role == role]
    known = {r.name: r.role for r in regions}
    names, problems = [], []
    for e in entries:
        sel = e.get("regions", "all")
        sel = of_role if sel == "all" else list(sel)
        if not sel:
            problems.append(f"a {role} entry selects no regions")
        for n in sel:
            if n not in known:
                problems.append(f"{n} does not exist")
            elif known[n] != role:
                problems.append(f"{n} is {known[n]}, not {role}")
        names.append(sel)
    return names, problems


# --------------------------------------------------------------------------- CAD face matching (Mechanical)

def match_faces(face_points: dict[int, np.ndarray], geo: PartGeometry, eps: float) -> dict[str, list[int]]:
    """Assign CAD faces of SPACE to regions from points on each face (its mesh nodes).

    A face belongs to a region when none of its points lies outside the region's K by more than eps and most lie
    strictly inside (signed distance > eps). Raises ValueError for a face that matches two regions.
    """
    owner: dict[int, str] = {}
    out: dict[str, list[int]] = {r.name: [] for r in geo.regions}
    for r in geo.regions:
        K = geo.region_keep(r.name)
        lo, hi = K.bounds
        for fid, pts in face_points.items():
            if not len(pts) or np.any(pts.min(axis=0) < lo - eps) or np.any(pts.max(axis=0) > hi + eps):
                continue
            sd = geom.signed_distance(K, pts)
            if sd.min() >= -eps and np.mean(sd > eps) > 0.5:
                if fid in owner:
                    raise ValueError(f"CAD face {fid} matches both {owner[fid]} and {r.name}")
                owner[fid] = r.name
                out[r.name].append(fid)
    return out


def check_matches(matches: dict[str, list[int]], face_area: dict[int, float], geo: PartGeometry,
                  area_tol: float) -> list[dict]:
    """Per region: matched face count and area against the Preserve region attribute. Hand-modelled keeps have
    no attribute; they only need at least one face. Each record carries ok."""
    out = []
    for r in geo.regions:
        ids = matches[r.name]
        area = float(sum(face_area[i] for i in ids))
        rec = {"region": r.name, "faces": ids, "face_count": len(ids), "area_mm2": area,
               "attribute_face_count": r.face_count, "attribute_area_mm2": r.area_mm2}
        if r.face_count is None:
            rec["ok"] = len(ids) > 0
        else:
            rec["ok"] = len(ids) == r.face_count and abs(area / r.area_mm2 - 1) <= area_tol
        out.append(rec)
    return out
