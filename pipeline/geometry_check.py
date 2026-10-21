"""Verify exported geometry and write keep.stl. Exits non-zero if any check fails; nothing downstream should run
on a failure.

    python -m pipeline.geometry_check --part <part>       # generic checks (plan Phase 5) on geometry/<part>

Reads space.stl, keep_parts/KEEP_*.stl, export.json and regions.json; writes keep.stl (union of KEEP_* parts) and
geometry_check.json. A part YAML with legacy_beam_checks also runs the Phase 1 analytic beam checks
(check_geometry, which the tests also run on the procedural stand-in from write_standin).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from pipeline import geom, regions as rg
from pipeline.config import bore_radius, bores, geometry_dir, keep_radius, load_part, load_run_config

POS_TOL = 0.01      # mm, bore/keep axis position (plan Phase 1)
VOL_TOL = 0.001     # relative


class Checks:
    def __init__(self):
        self.items = []

    def add(self, name: str, ok: bool, **detail):
        self.items.append({"check": name, "ok": bool(ok), **detail})

    @property
    def ok(self) -> bool:
        return all(c["ok"] for c in self.items)


def segments_around(mesh, cx: float, cz: float, r: float, y: float) -> int:
    """Distinct vertices on the circle of radius r about (cx, cz) in the plane y."""
    v = mesh.vertices
    on = (np.abs(v[:, 1] - y) < 1e-4) & (np.abs(np.hypot(v[:, 0] - cx, v[:, 2] - cz) - r) < 0.01)
    ang = np.round(np.degrees(np.arctan2(v[on, 2] - cz, v[on, 0] - cx)), 4)
    return len(np.unique(ang))


def check_geometry(gdir: Path, cfg: dict) -> tuple[Checks, object]:
    c = Checks()
    b = cfg["beam"]
    L, H, T = b["length"], b["height"], b["thickness"]
    r, R = bore_radius(cfg), keep_radius(cfg)
    exp = cfg["onshape_export"]
    bl = bores(cfg)

    space = geom.load_mesh(gdir / "space.stl")
    c.add("space watertight volume", space.is_volume)
    if len(space.faces) == 0:
        c.add("space.stl readable", False, reason="empty mesh")
        return c, None
    v_exp = L * H * T - len(bl) * math.pi * r * r * T
    c.add("space volume", abs(space.volume / v_exp - 1) <= VOL_TOL, volume=space.volume, expected=v_exp,
          rel_error=space.volume / v_exp - 1)
    bb_err = float(np.abs(space.bounds - np.array([[0, 0, 0], [L, T, H]])).max())
    c.add("space bounding box", bb_err <= POS_TOL, bounds=space.bounds.tolist(), max_error=bb_err)

    fits = {}
    for bo in bl:
        pts = geom.sample_band(space, bo.x, bo.z, 0.5 * r, r + 1.5, 0.5, T - 0.5)
        if len(pts) < 30:
            c.add(f"bore {bo.name} fit", False, reason="no bore surface found")
            continue
        f = geom.fit_cylinder_y(pts, bo.x, bo.z, r, 0.5 * T)
        off = max(math.hypot(f["cx"] + f["tilt_x"] * dy - bo.x, f["cz"] + f["tilt_z"] * dy - bo.z)
                  for dy in (-0.5 * T, 0.5 * T))
        fits[bo.name] = f
        c.add(f"bore {bo.name} fit", off <= POS_TOL and abs(f["radius"] - r) <= POS_TOL,
              axis_offset=off, radius_error=f["radius"] - r)

        # Tessellation actually delivered (verifies the export tolerance parameters took effect).
        n = segments_around(space, bo.x, bo.z, r, 0.0)
        sag = r * (1 - math.cos(math.pi / n)) if n else math.inf
        ang = 360.0 / n if n else math.inf
        c.add(f"bore {bo.name} tessellation",
              sag <= 1.05 * exp["stl_chord_tolerance"] and ang <= 1.05 * exp["stl_angle_tolerance"],
              segments=n, sagitta=sag, angle_deg=ang)

    kdir = gdir / "keep_parts"
    found = {p.stem: p for p in kdir.glob("KEEP_*.stl")}
    expected = {bo.keep_name for bo in bl}
    c.add("keep part names", set(found) == expected, missing=sorted(expected - set(found)),
          unexpected=sorted(set(found) - expected))

    parts = []
    v_keep = math.pi * R * R * T
    for bo in bl:
        if bo.keep_name not in found:
            continue
        k = geom.load_mesh(found[bo.keep_name])
        parts.append(k)
        c.add(f"{bo.keep_name} watertight volume", k.is_volume)
        c.add(f"{bo.keep_name} volume", abs(k.volume / v_keep - 1) <= VOL_TOL, volume=k.volume, expected=v_keep)
        v = k.vertices
        ring = v[np.abs(np.hypot(v[:, 0] - bo.x, v[:, 2] - bo.z) - R) < 0.1]
        if len(ring) < 10 or bo.name not in fits:
            c.add(f"{bo.keep_name} coaxial", False, reason="no rim vertices or no bore fit")
            continue
        kf = geom.fit_cylinder_y(ring, bo.x, bo.z, R, 0.5 * T)
        bf = fits[bo.name]
        off = max(math.hypot((kf["cx"] + kf["tilt_x"] * dy) - (bf["cx"] + bf["tilt_x"] * dy),
                             (kf["cz"] + kf["tilt_z"] * dy) - (bf["cz"] + bf["tilt_z"] * dy))
                  for dy in (-0.5 * T, 0.5 * T))
        y_err = max(abs(k.bounds[0][1]), abs(k.bounds[1][1] - T))
        c.add(f"{bo.keep_name} coaxial", off <= POS_TOL and y_err <= POS_TOL, axis_offset_to_bore=off,
              thickness_error=y_err)

    keep = None
    if parts and all(p.is_volume for p in parts):
        keep = geom.to_trimesh(geom.union_all(geom.to_manifold(p) for p in parts))
        c.add("keep.stl union is one manifold", keep.is_volume, bodies=len(keep.split(only_watertight=False)))
    else:
        c.add("keep.stl union is one manifold", False, reason="keep parts missing or not volumes")
    return c, keep


def check_part(gdir: Path, cfg: dict, part: dict) -> tuple[Checks, object]:
    """Generic checks for a part marked with preserve regions (plan Phase 5)."""
    c = Checks()
    tol = cfg["regions"]
    export = json.loads((gdir / "export.json").read_text(encoding="utf-8"))
    regions = rg.load_regions(gdir / "regions.json")

    space = geom.load_mesh(gdir / "space.stl")
    c.add("space watertight volume", space.is_volume)
    if len(space.faces) == 0:
        c.add("space.stl readable", False, reason="empty mesh")
        return c, None
    v_ref = export["space_volume_mm3"]
    c.add("space volume vs Onshape", abs(space.volume / v_ref - 1) <= tol["space_volume_tol"],
          volume=space.volume, onshape=v_ref, rel_error=space.volume / v_ref - 1)

    # Names and roles.
    kdir = gdir / "keep_parts"
    files = {p.stem: p for p in kdir.glob("KEEP_*.stl")}
    listed = [k.part for r in regions for k in r.keeps]
    c.add("keep names well formed and unique",
          all(rg.parse_keep_name(n) for n in listed) and len(set(listed)) == len(listed),
          bad=sorted(n for n in listed if not rg.parse_keep_name(n)))
    c.add("keep files match regions.json", set(files) == set(listed),
          missing=sorted(set(listed) - set(files)), unexpected=sorted(set(files) - set(listed)))
    roles = {r.role for r in regions}
    c.add("at least one FIX and one LOAD region", {"FIX", "LOAD"} <= roles, regions=[r.name for r in regions])
    fix_names, p1 = rg.resolve_bc_regions(part["supports"], "FIX", regions)
    load_names, p2 = rg.resolve_bc_regions(part["loads"], "LOAD", regions)
    c.add("part YAML regions exist with the right role", not (p1 + p2) and bool(fix_names) and bool(load_names),
          problems=p1 + p2, supports=fix_names, loads=load_names)

    # Per keep: watertight, straddles SPACE, S area vs attribute.
    m_space = geom.to_manifold(space)
    meshes = {}
    for r in regions:
        for k in r.keeps:
            if k.part not in files:
                continue
            km = geom.load_mesh(files[k.part])
            c.add(f"{k.part} watertight volume", km.is_volume)
            if not km.is_volume:
                continue
            meshes[k.part] = (r, km)
            m_k = geom.to_manifold(km)
            v_in = (m_k ^ m_space).volume()
            v_out = (m_k - m_space).volume()
            s_area = rg.surface_in_keep(space, km, tol["surface_tol"])
            c.add(f"{k.part} straddles its faces", v_in > 0 and v_out > 0 and s_area > 0,
                  in_space_mm3=v_in, outside_space_mm3=v_out, s_area_mm2=s_area)
            if k.area_mm2 is not None:
                c.add(f"{k.part} S area vs attribute", abs(s_area / k.area_mm2 - 1) <= tol["area_tol"],
                      s_area_mm2=s_area, attribute_mm2=k.area_mm2, rel_error=s_area / k.area_mm2 - 1)

    by_role = {role: [geom.to_manifold(km) for (r, km) in meshes.values() if r.role == role] for role in rg.ROLES}
    if by_role["FIX"] and by_role["LOAD"]:
        ov = (geom.union_all(by_role["FIX"]) ^ geom.union_all(by_role["LOAD"])).volume()
        c.add("FIX and LOAD keeps don't overlap", ov <= tol["overlap_tol"], overlap_mm3=ov)

    keep = None
    if meshes and len(meshes) == len(listed):
        keep = geom.to_trimesh(geom.union_all(geom.to_manifold(km) for (_, km) in meshes.values()))
        c.add("keep.stl union is one manifold", keep.is_volume, bodies=len(keep.split(only_watertight=False)))
    else:
        c.add("keep.stl union is one manifold", False, reason="keep parts missing or not volumes")

    if part.get("legacy_beam_checks"):
        legacy, _ = check_geometry(gdir, cfg)
        for it in legacy.items:
            c.add(f"beam: {it['check']}", it["ok"], **{k: v for k, v in it.items() if k not in ("check", "ok")})
    return c, keep


def write_standin(gdir: Path, cfg: dict) -> None:
    (gdir / "keep_parts").mkdir(parents=True, exist_ok=True)
    geom.to_trimesh(geom.make_space(cfg)).export(gdir / "space.stl")
    for name, m in geom.make_keep_parts(cfg).items():
        geom.to_trimesh(m).export(gdir / "keep_parts" / f"{name}.stl")
    (gdir / "export.json").write_text(json.dumps({"source": "standin (pipeline.geom procedural)"}, indent=2),
                                      encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", required=True, help="config/parts/<part>.yaml; checks geometry/<part>")
    ap.add_argument("--geometry-dir", type=Path, help="default geometry/<part>")
    a = ap.parse_args(argv)
    cfg = load_run_config(a.part)
    a.geometry_dir = a.geometry_dir or geometry_dir(a.part)
    checks, keep = check_part(a.geometry_dir, cfg, load_part(a.part))
    if keep is not None and checks.ok:
        keep.export(a.geometry_dir / "keep.stl")
    (a.geometry_dir / "geometry_check.json").write_text(
        json.dumps({"ok": checks.ok, "checks": checks.items}, indent=2), encoding="utf-8")

    for it in checks.items:
        extra = {k: (round(float(v), 6) if isinstance(v, float) else v) for k, v in it.items() if k not in ("check", "ok")}
        print(f"{'PASS' if it['ok'] else 'FAIL'}  {it['check']:44s} {extra if extra else ''}")
    print(f"\n{'ALL CHECKS PASSED' if checks.ok else 'GEOMETRY CHECK FAILED'}"
          f"{'; wrote keep.stl' if checks.ok else ''}")
    return 0 if checks.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
