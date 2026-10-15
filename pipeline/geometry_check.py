"""Verify the exported geometry against base.yaml and write keep.stl (plan Phase 1 checks).

    python -m pipeline.geometry_check [--geometry-dir geometry]
    python -m pipeline.geometry_check --write-standin     # procedural SPACE/KEEP_* for dry runs, then check

Reads space.stl and keep_parts/KEEP_*.stl; writes keep.stl (union of KEEP_* parts) and
geometry_check.json. Exits non-zero if any check fails. Nothing downstream should run on a failure.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from pipeline import geom
from pipeline.config import REPO, bore_radius, bores, keep_radius, load_config

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


def write_standin(gdir: Path, cfg: dict) -> None:
    (gdir / "keep_parts").mkdir(parents=True, exist_ok=True)
    geom.to_trimesh(geom.make_space(cfg)).export(gdir / "space.stl")
    for name, m in geom.make_keep_parts(cfg).items():
        geom.to_trimesh(m).export(gdir / "keep_parts" / f"{name}.stl")
    (gdir / "export.json").write_text(json.dumps({"source": "standin (pipeline.geom procedural)"}, indent=2),
                                      encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--geometry-dir", type=Path, default=REPO / "geometry")
    ap.add_argument("--write-standin", action="store_true")
    ap.add_argument("--config", action="append", default=[])
    a = ap.parse_args(argv)
    cfg = load_config(*a.config)
    if a.write_standin:
        write_standin(a.geometry_dir, cfg)

    checks, keep = check_geometry(a.geometry_dir, cfg)
    if keep is not None and checks.ok:
        keep.export(a.geometry_dir / "keep.stl")
    (a.geometry_dir / "geometry_check.json").write_text(
        json.dumps({"ok": checks.ok, "checks": checks.items}, indent=2), encoding="utf-8")

    for it in checks.items:
        extra = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in it.items() if k not in ("check", "ok")}
        print(f"{'PASS' if it['ok'] else 'FAIL'}  {it['check']:38s} {extra if extra else ''}")
    print(f"\n{'ALL CHECKS PASSED' if checks.ok else 'GEOMETRY CHECK FAILED'}"
          f"{'; wrote keep.stl' if checks.ok else ''}")
    return 0 if checks.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
