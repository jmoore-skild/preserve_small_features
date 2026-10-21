"""Geometry metrics (plan Phase 6 and Section 9): preserved-surface deviation, connectivity, seam necks, volumes.

    python -m pipeline.measure --run-dir runs/<part>/M3_h4               # measures final.stl (rebuilt)
    python -m pipeline.measure --run-dir runs/<part>/M0_h4 --raw         # measures opt.stl, report-only

Generic metrics work on any part's regions. Parts with legacy_beam_metrics also get the Phase 4 beam-only
metrics (bore fits, washer seats, cylindrical seam shells) under legacy_beam, until Gate 4 retires them.
Writes metrics.json in the run directory.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import trimesh

from pipeline import geom, regions as rg
from pipeline.config import bore_radius, bores, circular_segments, geometry_dir, keep_radius, read_run_config
from pipeline.rebuild import classify_components


# --------------------------------------------------------------------------- bore fidelity (beam only)

def bore_fidelity(mesh: trimesh.Trimesh, cfg: dict, detached: frozenset = frozenset()) -> list[dict]:
    mc = cfg["measure"]
    T = cfg["beam"]["thickness"]
    r = bore_radius(cfg)
    tol = mc["bore_fidelity_tol"]
    out = []
    for b in bores(cfg):
        pts = geom.sample_band(mesh, b.x, b.z, mc["bore_band_inner_factor"] * r, r + mc["bore_band_outer_offset"],
                               mc["bore_band_y_margin"], T - mc["bore_band_y_margin"])
        rec = {"bore": b.name, "n_points": int(len(pts)), "coverage": geom.angular_coverage(pts, b.x, b.z)}
        if b.name in detached:     # PASSIVE zone in islands.stl: recorded, not measured
            rec.update(status="detached", radius_error=None, axis_offset=None, max_radial_deviation=None, ok=None)
            out.append(rec)
            continue
        if len(pts) < mc["bore_min_vertices"] or rec["coverage"] < 0.5:
            rec.update(status="missing", radius_error=None, axis_offset=None, max_radial_deviation=None, ok=False)
            out.append(rec)
            continue
        fit = geom.fit_cylinder_y(pts, b.x, b.z, r, 0.5 * T)
        # Axis offset from nominal, worst of the two face planes (captures tilt).
        offs = [math.hypot(fit["cx"] + fit["tilt_x"] * dy - b.x, fit["cz"] + fit["tilt_z"] * dy - b.z)
                for dy in (-0.5 * T, 0.5 * T)]
        dev = np.abs(np.hypot(pts[:, 0] - b.x, pts[:, 2] - b.z) - r)
        rec.update(status="fit", radius=fit["radius"], radius_error=fit["radius"] - r, axis_offset=max(offs),
                   max_radial_deviation=float(dev.max()), fit_rms=fit["rms"])
        rec["ok"] = bool(abs(rec["radius_error"]) <= tol and rec["axis_offset"] <= tol
                         and rec["max_radial_deviation"] <= tol)
        out.append(rec)
    return out


# --------------------------------------------------------------------------- seat flatness (beam only)

def _annulus_grid(r_in: float, r_out: float, step: float) -> np.ndarray:
    # Irrational offset keeps rays off facet edges and vertices.
    g = np.arange(-r_out, r_out + step, step) + step * 0.3819660113
    X, Z = np.meshgrid(g, g)
    rho = np.hypot(X, Z)
    sel = (rho >= r_in) & (rho <= r_out)
    return np.column_stack([X[sel], Z[sel]])


def seat_flatness(mesh: trimesh.Trimesh, cfg: dict, detached: frozenset = frozenset()) -> list[dict]:
    mc = cfg["measure"]
    T = cfg["beam"]["thickness"]
    r = bore_radius(cfg)
    rw = cfg["washer"]["diameter"] / 2.0
    tol = mc["seat_flatness_tol"]
    grid = _annulus_grid(r + 0.05, rw, mc["seat_grid_step"])
    standoff = 5.0
    tri = mesh.triangles
    out = []
    for b in bores(cfg):
        lo, hi = tri.min(axis=1), tri.max(axis=1)
        near = ((hi[:, 0] >= b.x - rw - 0.5) & (lo[:, 0] <= b.x + rw + 0.5)
                & (hi[:, 2] >= b.z - rw - 0.5) & (lo[:, 2] <= b.z + rw + 0.5))
        sub = mesh.submesh([np.flatnonzero(near)], append=True) if near.any() else None
        for face_y, direction in ((0.0, 1.0), (T, -1.0)):
            n = len(grid)
            rec = {"bore": b.name, "face_y": face_y, "n_rays": n}
            if b.name in detached:
                rec.update(max_deviation=None, misses=None, ok=None, status="detached")
                out.append(rec)
                continue
            if sub is None:
                rec.update(max_deviation=None, misses=n, ok=False)
                out.append(rec)
                continue
            origins = np.column_stack([b.x + grid[:, 0], np.full(n, face_y - direction * standoff), b.z + grid[:, 1]])
            dirs = np.tile([0.0, direction, 0.0], (n, 1))
            loc, ray_idx, _ = sub.ray.intersects_location(origins, dirs, multiple_hits=True)
            first = np.full(n, np.nan)
            if len(loc):
                dist = (loc[:, 1] - origins[ray_idx, 1]) * direction
                order = np.lexsort((dist, ray_idx))
                ray_sorted = ray_idx[order]
                firsts = order[np.r_[True, ray_sorted[1:] != ray_sorted[:-1]]]
                first[ray_idx[firsts]] = loc[firsts, 1]
            hit = ~np.isnan(first)
            dev = np.abs(first[hit] - face_y)
            rec.update(max_deviation=float(dev.max()) if hit.any() else None, misses=int((~hit).sum()))
            rec["ok"] = bool(rec["misses"] == 0 and rec["max_deviation"] is not None and rec["max_deviation"] <= tol)
            out.append(rec)
    return out


# --------------------------------------------------------------------------- seam shells (beam only)

def seam_profiles(mesh: trimesh.Trimesh, cfg: dict, h: float) -> list[dict]:
    """Material cross-section through thin cylindrical shells around each keep cylinder.

    Shell k spans [R + k·s, R + (k+1)·s] (k = -1 is the last shell inside the keep zone).
    Cross-section ≈ shell volume / s. A neck is a valley: dip_k = 1 − A_k / min(max A before, max A after).
    """
    mc = cfg["measure"]
    T = cfg["beam"]["thickness"]
    R = keep_radius(cfg)
    s = mc["seam_shell_step"]
    n_seg = circular_segments(cfg, R)
    m = geom.to_manifold(mesh)
    all_bores = bores(cfg)
    out = []
    for b in all_bores:
        gaps = [math.hypot(b.x - o.x, b.z - o.z) - 2 * R for o in all_bores if o is not b]
        max_off = min(2.0 * h, 0.5 * min(gaps))
        n_out = max(1, int(math.floor(max_off / s + 1e-9)))
        radii, areas = [], []
        for k in range(-1, n_out):
            r_in, r_out = R + k * s, R + (k + 1) * s
            shell = geom.ring_y(b.x, b.z, r_in, r_out, -1.0, T + 1.0, n_seg)
            radii.append(0.5 * (r_in + r_out))
            areas.append((m ^ shell).volume() / s)
        A = np.array(areas)
        dips = []
        for k in range(1, len(A) - 1):
            ref = min(A[:k].max(), A[k + 1:].max())
            dips.append(max(0.0, 1.0 - A[k] / ref) if ref > 0 else 0.0)
        max_dip = max(dips) if dips else 0.0
        out.append({"bore": b.name, "max_offset": max_off, "radii": radii, "areas": A.tolist(),
                    "max_dip": max_dip, "flagged": bool(max_dip > mc["seam_dip_flag"])})
    return out


# --------------------------------------------------------------------------- preserved surface (generic)

def preserved_surface(mesh: trimesh.Trimesh, geo: rg.PartGeometry, cfg: dict,
                      detached: frozenset = frozenset()) -> list[dict]:
    """Per region: sample P (SPACE's surface inside or on K) and measure each sample's distance to `mesh`'s
    surface. A rebuilt part reproduces P exactly, so every distance should be ~0."""
    mc = cfg["measure"]
    tol = mc["surface_tol"]
    out = []
    for r in geo.regions:
        pts = rg.preserved_samples(geo.space, geo.region_keep(r.name), cfg["regions"]["surface_tol"],
                                   mc["surface_spacing"])
        rec = {"region": r.name, "n_samples": int(len(pts))}
        if r.name in detached:      # a PASSIVE region in islands.stl: recorded, not measured
            rec.update(status="detached", max_deviation=None, p99_deviation=None, beyond_fraction=None, ok=None)
        elif not len(pts):
            rec.update(status="no_samples", max_deviation=None, p99_deviation=None, beyond_fraction=None, ok=False)
        else:
            d = geom.distance(mesh, pts)
            rec.update(status="measured", max_deviation=float(d.max()), p99_deviation=float(np.percentile(d, 99)),
                       beyond_fraction=float(np.mean(d > tol)), ok=bool(d.max() <= tol))
        out.append(rec)
    return out


# --------------------------------------------------------------------------- seam necks (generic)

def _keep_gaps(geo: rg.PartGeometry, spacing: float = 0.25) -> dict[str, float]:
    """Per keep part, the distance to the nearest other keep part (0 if they touch or overlap). Measured from
    surface samples, so it can only overestimate, by at most about spacing²/(8 × curvature radius)."""
    names = list(geo.keep_parts)
    out = {}
    for n in names:
        others = [geo.keep_parts[o] for o in names if o != n]
        if not others:
            out[n] = math.inf
            continue
        other = trimesh.util.concatenate(others)
        pts, _ = geom.sample_surface(geo.keep_parts[n], spacing)
        pts = np.vstack([pts, geo.keep_parts[n].vertices])
        out[n] = 0.0 if geom.inside(other, pts).any() else float(geom.distance(other, pts).min())
    return out


def seam_bands(mesh: trimesh.Trimesh, geo: rg.PartGeometry, cfg: dict, h: float) -> list[dict]:
    """Per keep part: the fraction of SPACE's volume that `mesh` keeps, in distance bands around K.

    Band k holds the points of SPACE at distance [k·s, (k+1)·s) outside K, for k = -1 (the last band inside K)
    up to the smaller of 2h and half the gap to the nearest other keep. Volumes come from grid sampling.
    A neck is a valley in the fractions: dip_k = 1 − f_k / min(max f before, max f after).
    """
    mc = cfg["measure"]
    s, pitch = mc["seam_shell_step"], mc["seam_grid_step"]
    gaps = _keep_gaps(geo)
    out = []
    for name, K in geo.keep_parts.items():
        max_off = min(2.0 * h, 0.5 * gaps[name])
        n_out = max(1, int(math.floor(max_off / s + 1e-9)))
        lo, hi = K.bounds
        reach = n_out * s + pitch
        # Irrational offset keeps grid points off the CAD's planes.
        axes = [np.arange(lo[i] - reach, hi[i] + reach, pitch) + pitch * 0.3819660113 for i in range(3)]
        pts = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
        d = -geom.signed_distance(K, pts)                       # positive outside K
        sel = (d >= -s) & (d < n_out * s)
        pts, d = pts[sel], d[sel]
        in_space = geom.inside(geo.space, pts)
        pts, d = pts[in_space], d[in_space]
        kept = geom.inside(mesh, pts)
        band = np.floor(d / s).astype(int)
        offsets, fracs = [], []
        for k in range(-1, n_out):
            in_band = band == k
            if in_band.sum():
                offsets.append((k + 0.5) * s)
                fracs.append(float(kept[in_band].mean()))
        f = np.array(fracs)
        dips = []
        for k in range(1, len(f) - 1):
            ref = min(f[:k].max(), f[k + 1:].max())
            dips.append(max(0.0, 1.0 - f[k] / ref) if ref > 0 else 0.0)
        max_dip = max(dips) if dips else 0.0
        region = next(r.name for r in geo.regions if any(k.part == name for k in r.keeps))
        out.append({"keep": name, "region": region, "max_offset": max_off, "gap_to_nearest_keep": gaps[name],
                    "offsets": offsets, "fractions": fracs, "max_dip": max_dip,
                    "flagged": bool(max_dip > mc["seam_dip_flag"])})
    return out


# --------------------------------------------------------------------------- driver

def legacy_detached(cfg: dict, geo: rg.PartGeometry, detached_regions: frozenset) -> frozenset:
    """Beam bores (by Phase 4 name) whose region is detached: the bore's keep-wall probe lies in that region's K."""
    rho = 0.5 * (bore_radius(cfg) + keep_radius(cfg))
    y = 0.5 * cfg["beam"]["thickness"]
    out = set()
    for b in bores(cfg):
        p = np.array([[b.x + rho, y, b.z]])
        if any(geom.inside(geo.region_keep(n), p)[0] for n in detached_regions):
            out.add(b.name)
    return frozenset(out)


def measure(mesh: trimesh.Trimesh, geo: rg.PartGeometry, cfg: dict, h: float, rebuilt: bool = True,
            rebuild_report: dict | None = None, legacy: bool | None = None) -> dict:
    """All geometry metrics. For raw (non-rebuilt) meshes, fidelity results are report-only.
    `legacy` adds the beam-only bore and seat metrics (default: the part YAML's legacy_beam_metrics)."""
    legacy = cfg.get("legacy_beam_metrics", False) if legacy is None else legacy
    if rebuild_report is not None:
        main = next(c["index"] for c in rebuild_report["components"] if c["role"] == "main")
        detached = frozenset(n for n, i in rebuild_report["region_body"].items() if i != main)
        conn = {"fix_load_connected": rebuild_report["fix_load_connected"],
                "passive_attached": rebuild_report["passive_attached"],
                "n_bodies": len(rebuild_report["components"])}
        volumes = rebuild_report["volumes"]
    else:
        detached = frozenset()
        try:
            cls = classify_components(mesh, geo)
            conn = {"fix_load_connected": cls["fix_load_connected"], "passive_attached": cls["passive_attached"],
                    "n_bodies": len(cls["bodies"])}
        except ValueError as e:     # raw mesh that manifold rejects
            conn = {"fix_load_connected": None, "passive_attached": None, "n_bodies": None, "error": str(e)}
        volumes = {"mesh": float(mesh.volume) if mesh.is_volume else None}

    surface = preserved_surface(mesh, geo, cfg, detached)
    seams = seam_bands(mesh, geo, cfg, h)
    measured = [r for r in surface if r["ok"] is not None]
    out = {
        "rebuilt": rebuilt,
        "h": h,
        "preserved_surface": surface,
        "connectivity": conn,
        "seams": seams,
        "volumes": volumes,
        "pass": {
            # Raw meshes are reported only (plan Section 9).
            "preserved_surface": all(r["ok"] for r in measured) if rebuilt else None,
            "load_path": conn["fix_load_connected"],
        },
        "summary": {
            "surface_max_deviation": _max_abs([r["max_deviation"] for r in surface]),
            "surface_max_p99_deviation": _max_abs([r["p99_deviation"] for r in surface]),
            "surface_max_beyond_fraction": _max_abs([r["beyond_fraction"] for r in surface]),
            "surface_regions_failing": [r["region"] for r in measured if not r["ok"]],
            "detached": sorted(detached),
            "seam_flags": [r["keep"] for r in seams if r["flagged"]],
        },
    }
    if legacy:
        bd = legacy_detached(cfg, geo, detached)
        bore = bore_fidelity(mesh, cfg, bd)
        seat = seat_flatness(mesh, cfg, bd)
        try:
            shells = seam_profiles(mesh, cfg, h)
        except ValueError as e:
            shells = [{"error": str(e)}]
        out["legacy_beam"] = {"bores": bore, "seats": seat, "seam_shells": shells}
        out["pass"]["bore_fidelity"] = all(r["ok"] for r in bore if r["ok"] is not None) if rebuilt else None
        out["pass"]["seat_flatness"] = all(r["ok"] for r in seat if r["ok"] is not None) if rebuilt else None
        out["summary"].update({
            "bore_max_abs_radius_error": _max_abs([r["radius_error"] for r in bore]),
            "bore_max_axis_offset": _max_abs([r["axis_offset"] for r in bore]),
            "bore_max_radial_deviation": _max_abs([r["max_radial_deviation"] for r in bore]),
            "bores_missing": [r["bore"] for r in bore if r["status"] == "missing"],
            "seat_max_deviation": _max_abs([r["max_deviation"] for r in seat]),
            "seat_misses": int(sum(r["misses"] or 0 for r in seat)),
            "legacy_seam_flags": [r["bore"] for r in shells if r.get("flagged")],
        })
    return out


def _max_abs(vals):
    vals = [abs(v) for v in vals if v is not None]
    return max(vals) if vals else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--h", type=float, help="global mesh size of the TO run (mm); default: the run's run.h")
    ap.add_argument("--raw", action="store_true", help="measure opt.stl as-is (M0), report-only")
    ap.add_argument("--geometry-dir", type=Path, help="default: geometry/<part of the run>")
    a = ap.parse_args(argv)

    cfg = read_run_config(a.run_dir)
    geo = rg.load_part_geometry(a.geometry_dir or geometry_dir(cfg["part"]))
    if a.raw:
        mesh, report = geom.load_mesh(a.run_dir / "opt.stl"), None
    else:
        report = json.loads((a.run_dir / "rebuild.json").read_text(encoding="utf-8"))
        if report.get("status") != "ok":
            print(f"rebuild failed; nothing to measure: {report.get('reason')}")
            return 1
        mesh = geom.load_mesh(a.run_dir / "final.stl")
    h = a.h if a.h is not None else cfg["run"]["h"]
    metrics = measure(mesh, geo, cfg, h, rebuilt=not a.raw, rebuild_report=report)
    (a.run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps({"pass": metrics["pass"], "summary": metrics["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
