"""Synthetic `opt` meshes with planted defects (plan Phase 2).

Base shapes:
  exact  — lightened Warren-truss beam (box minus lightening holes minus true bores), built with manifold.
  coarse — the exact beam's signed distance sampled on a `voxel_pitch` grid, marching tetrahedra, Taubin
           smoothing: mimics a coarse TO export (faceted/filled bores, rounded edges).

Defects (each a function opt -> opt):
  a  undersized, faceted bores           b  outward bulges past the y = T face (one over a washer seat)
  c  necked seam next to a FIX keep zone d  PASSIVE region cut free as an island
  e  non-watertight opt                  split  FIX separated from LOAD (hard fail)

    python -m pipeline.synth --out runs/synth            # writes one opt.stl per case
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import manifold3d as m3d
import numpy as np
import trimesh
from scipy.interpolate import RegularGridInterpolator

from pipeline import geom
from pipeline.config import bore_radius, bores, keep_radius, load_config

# --------------------------------------------------------------------------- base shapes


def _strip(p0, p1, w) -> m3d.CrossSection:
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    d = (p1 - p0) / np.linalg.norm(p1 - p0)
    n = np.array([-d[1], d[0]]) * w / 2
    ext = d * w   # overrun so strips merge into the chords
    return m3d.CrossSection([[p0 - ext + n, p1 + ext + n, p1 + ext - n, p0 - ext - n]], m3d.FillRule.NonZero)


def lightened_cross_section(cfg: dict) -> m3d.CrossSection:
    """XZ profile of the lightened beam (true bores not included)."""
    b, sc = cfg["beam"], cfg["synth"]
    L, H = b["length"], b["height"]
    c, w = sc["chord"], sc["web_width"]
    xa, xb = sc["web_x_range"]
    n = sc["panels"]
    p = (xb - xa) / n
    web = m3d.CrossSection.square([xb - xa, H - 2 * c]).translate([xa, c])
    strips = []
    for i in range(n):
        x0 = xa + i * p
        strips.append(_strip((x0, c), (x0 + p / 2, H - c), w))
        strips.append(_strip((x0 + p / 2, H - c), (x0 + p, c), w))
    boss_r = keep_radius(cfg) + sc["boss_margin"]
    bosses = [m3d.CrossSection.circle(boss_r, 64).translate([bo.x, bo.z]) for bo in bores(cfg)]
    holes = web - m3d.CrossSection.batch_boolean(strips + bosses, m3d.OpType.Add)
    return _snap(m3d.CrossSection.square([L, H]) - holes)


def _snap(cs: m3d.CrossSection, tol: float = 1e-4) -> m3d.CrossSection:
    """Drop near-duplicate polygon vertices left by Clipper's integer rounding (e.g. 54 vs 53.99999999).

    Left in, they become sub-float32 edges that break watertightness once the mesh goes through STL.
    """
    polys = []
    for poly in cs.to_polygons():
        kept = [poly[0]]
        for pt in poly[1:]:
            if np.hypot(*(pt - kept[-1])) > tol:
                kept.append(pt)
        if len(kept) > 2 and np.hypot(*(kept[0] - kept[-1])) <= tol:
            kept.pop()
        if len(kept) >= 3:
            polys.append(np.array(kept))
    return m3d.CrossSection(polys)


def exact_opt(cfg: dict) -> m3d.Manifold:
    T = cfg["beam"]["thickness"]
    return geom.prism_y(lightened_cross_section(cfg), 0.0, T) ^ geom.make_space(cfg)


def coarse_opt(cfg: dict) -> trimesh.Trimesh:
    """Signed distance of the exact beam on a coarse grid → marching cubes → Taubin smoothing."""
    sc = cfg["synth"]
    pitch = sc["voxel_pitch"]
    exact = geom.to_trimesh(exact_opt(cfg))
    lo, hi = exact.bounds
    # Half-pitch offset so grid nodes never sit exactly on the planar faces.
    axes = [np.arange(lo[i] - 1.5 * pitch, hi[i] + 2.0 * pitch, pitch) for i in range(3)]
    X, Y, Z = np.meshgrid(*axes, indexing="ij")
    pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
    with np.errstate(divide="ignore", invalid="ignore"):   # degenerate slivers from manifold output
        sd = trimesh.proximity.signed_distance(exact, pts).reshape(X.shape)   # positive inside
    # Surface extraction from the coarse samples. skimage's marching cubes leaves non-manifold
    # edges at ambiguous cells on thin members; manifold's marching tetrahedra (BCC grid) over the
    # trilinear interpolant of the same samples is guaranteed manifold.
    interp = RegularGridInterpolator(axes, sd, bounds_error=False, fill_value=-pitch)
    bounds = [a[0] for a in axes] + [a[-1] for a in axes]
    mesh = geom.to_trimesh(m3d.Manifold.level_set(lambda x, y, z: float(interp((x, y, z))), bounds, pitch))
    trimesh.smoothing.filter_taubin(mesh, lamb=0.5, nu=0.53, iterations=sc["taubin_iterations"])
    if not mesh.is_volume:
        raise RuntimeError("coarse synthetic opt is not a closed volume")
    return mesh


# --------------------------------------------------------------------------- defects


def _bore(cfg, zone, i=1):
    return next(b for b in bores(cfg) if b.zone == zone and b.index == i)


def defect_a(opt: m3d.Manifold, cfg: dict) -> m3d.Manifold:
    """Fill every bore and re-cut it as an undersized octagon (r − 0.65 mm)."""
    T = cfg["beam"]["thickness"]
    r = bore_radius(cfg)
    fill = geom.union_all(geom.cylinder_y(b.x, b.z, r + 0.3, 0.0, T, 64) for b in bores(cfg))
    cut = geom.union_all(geom.cylinder_y(b.x, b.z, r - 0.65, -1.0, T + 1.0, 8) for b in bores(cfg))
    return (opt + fill) - cut


def defect_b(opt: m3d.Manifold, cfg: dict) -> m3d.Manifold:
    """Two bulges past the y = T face: one on the FIX_3 washer annulus, one on the top chord."""
    T = cfg["beam"]["thickness"]
    r, rw = bore_radius(cfg), cfg["washer"]["diameter"] / 2
    b = _bore(cfg, "FIX", 3)
    rho = 0.5 * (r + rw)
    seat_bump = m3d.Manifold.sphere(1.0, 48).scale([1.0, 0.5, 1.0]).translate([b.x + rho, T, b.z])
    L, H = cfg["beam"]["length"], cfg["beam"]["height"]
    chord_bump = m3d.Manifold.sphere(1.0, 48).scale([6.0, 0.8, 2.0]).translate([0.75 * L, T, H - 3.0])
    return opt + seat_bump + chord_bump


def defect_c(opt: m3d.Manifold, cfg: dict) -> m3d.Manifold:
    """Annular gap R .. R+1.5 around FIX_4, bridged by two 1 mm ligaments (±Z)."""
    T = cfg["beam"]["thickness"]
    R = keep_radius(cfg)
    b = _bore(cfg, "FIX", 4)
    ring = m3d.CrossSection.circle(R + 1.5, 128) - m3d.CrossSection.circle(R - 0.5, 128)
    bridge = m3d.CrossSection.square([1.0, 2 * (R + 2.0)]).translate([-0.5, -(R + 2.0)])
    gap = (ring - bridge).translate([b.x, b.z])
    return opt - geom.prism_y(gap, -1.0, T + 1.0)


def defect_d(opt: m3d.Manifold, cfg: dict) -> m3d.Manifold:
    """2 mm moat around both PASSIVE bores (stadium, offset R+2 .. R+4) cutting that region free."""
    T = cfg["beam"]["thickness"]
    R = keep_radius(cfg)
    ps = [b for b in bores(cfg) if b.zone == "PASSIVE"]

    def stadium(rad):
        return m3d.CrossSection.batch_hull([m3d.CrossSection.circle(rad, 128).translate([b.x, b.z]) for b in ps])

    moat = stadium(R + 4.0) - stadium(R + 2.0)
    return opt - geom.prism_y(moat, -1.0, T + 1.0)


def defect_split(opt: m3d.Manifold, cfg: dict) -> m3d.Manifold:
    """Full-height 2 mm slot at 3/4 span: FIX and LOAD end up in different bodies."""
    b = cfg["beam"]
    x = 0.75 * b["length"]
    slot = m3d.Manifold.cube([2.0, b["thickness"] + 2, b["height"] + 2]).translate([x, -1, -1])
    return opt - slot


def defect_e(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Delete a patch of faces: no longer watertight."""
    mesh = mesh.copy()
    c = mesh.triangles_center
    target = c[np.argmax(c[:, 0])]            # a patch at the tip end
    drop = np.argsort(np.linalg.norm(c - target, axis=1))[:12]
    keep = np.ones(len(mesh.faces), bool)
    keep[drop] = False
    mesh.update_faces(keep)
    mesh.remove_unreferenced_vertices()
    return mesh


DEFECTS = {"a": defect_a, "b": defect_b, "c": defect_c, "d": defect_d, "split": defect_split}


def make_case(cfg: dict, base: str, defects: str = "") -> trimesh.Trimesh:
    """base in {"exact", "coarse"}; defects a string of letters from 'abcde' and/or 'split' (e.g. "abcd")."""
    m = exact_opt(cfg) if base == "exact" else geom.to_manifold(coarse_opt(cfg))
    tokens = ["split"] if "split" in defects else []
    tokens += [ch for ch in defects.replace("split", "") if ch in "abcd"]
    for t in tokens:
        m = DEFECTS[t](m, cfg)
    mesh = geom.to_trimesh(m)
    if "e" in defects.replace("split", ""):
        mesh = defect_e(mesh)
    return mesh


CASES = [("exact", ""), ("coarse", ""), *[(b, d) for b in ("exact", "coarse") for d in "abcde"],
         ("exact", "abcd"), ("coarse", "abcd"), ("exact", "split")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("runs/synth"))
    ap.add_argument("--config", action="append", default=[])
    a = ap.parse_args(argv)
    cfg = load_config(*a.config)
    space_vol = geom.make_space(cfg).volume()
    for base, d in CASES:
        name = f"{base}_{d or 'clean'}"
        mesh = make_case(cfg, base, d)
        out = a.out / name
        out.mkdir(parents=True, exist_ok=True)
        mesh.export(out / "opt.stl")
        print(f"{name:16s} V/V_space = {mesh.volume / space_vol:.3f}  watertight={mesh.is_watertight}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
