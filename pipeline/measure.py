"""Geometry metrics (plan Section 9): bore fidelity, seat flatness, connectivity, seam necks, volumes.

    python -m pipeline.measure --run-dir runs/M3_h4 --h 4                # measures final.stl (rebuilt)
    python -m pipeline.measure --run-dir runs/M0_h4 --h 4 --raw          # measures opt.stl, report-only

Writes metrics.json in the run directory.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import trimesh

from pipeline import geom
from pipeline.config import bore_radius, bores, circular_segments, keep_radius, load_config
from pipeline.rebuild import classify_components


# --------------------------------------------------------------------------- bore fidelity

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


# --------------------------------------------------------------------------- seat flatness

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


# --------------------------------------------------------------------------- seam necks

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


# --------------------------------------------------------------------------- driver

def measure(mesh: trimesh.Trimesh, cfg: dict, h: float, rebuilt: bool = True,
            rebuild_report: dict | None = None) -> dict:
    """All geometry metrics. For raw (non-rebuilt) meshes, bore/seat results are report-only."""
    detached = frozenset()
    if rebuild_report is not None:
        main = next(c["index"] for c in rebuild_report["components"] if c["role"] == "main")
        detached = frozenset(n for n, i in rebuild_report["probe_body"].items() if i != main)
    bore = bore_fidelity(mesh, cfg, detached)
    seat = seat_flatness(mesh, cfg, detached)

    if rebuild_report is not None:
        conn = {"fix_load_connected": rebuild_report["fix_load_connected"],
                "passive_attached": rebuild_report["passive_attached"],
                "n_bodies": len(rebuild_report["components"])}
        volumes = rebuild_report["volumes"]
    else:
        try:
            cls = classify_components(mesh, cfg)
            conn = {"fix_load_connected": cls["fix_load_connected"], "passive_attached": cls["passive_attached"],
                    "n_bodies": len(cls["bodies"])}
        except ValueError as e:     # raw mesh that manifold rejects
            conn = {"fix_load_connected": None, "passive_attached": None, "n_bodies": None, "error": str(e)}
        volumes = {"mesh": float(mesh.volume) if mesh.is_volume else None}

    try:
        seams = seam_profiles(mesh, cfg, h)
    except ValueError as e:
        seams = [{"error": str(e)}]

    bores_ok = all(r["ok"] for r in bore if r["ok"] is not None)
    seats_ok = all(r["ok"] for r in seat if r["ok"] is not None)
    return {
        "rebuilt": rebuilt,
        "h": h,
        "bores": bore,
        "seats": seat,
        "connectivity": conn,
        "seams": seams,
        "volumes": volumes,
        "pass": {
            # Raw M0 meshes are reported only (plan Section 9).
            "bore_fidelity": bores_ok if rebuilt else None,
            "seat_flatness": seats_ok if rebuilt else None,
            "load_path": conn["fix_load_connected"],
        },
        "summary": {
            "bore_max_abs_radius_error": _max_abs([r["radius_error"] for r in bore]),
            "bore_max_axis_offset": _max_abs([r["axis_offset"] for r in bore]),
            "bore_max_radial_deviation": _max_abs([r["max_radial_deviation"] for r in bore]),
            "bores_missing": [r["bore"] for r in bore if r["status"] == "missing"],
            "seat_max_deviation": _max_abs([r["max_deviation"] for r in seat]),
            "seat_misses": int(sum(r["misses"] or 0 for r in seat)),
            "detached": sorted(detached),
            "seam_flags": [r["bore"] for r in seams if r.get("flagged")],
        },
    }


def _max_abs(vals):
    vals = [abs(v) for v in vals if v is not None]
    return max(vals) if vals else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--h", required=True, type=float, help="global mesh size of the TO run (mm)")
    ap.add_argument("--raw", action="store_true", help="measure opt.stl as-is (M0), report-only")
    ap.add_argument("--config", action="append", default=[])
    a = ap.parse_args(argv)

    cfg = load_config(*a.config)
    if a.raw:
        mesh, report = geom.load_mesh(a.run_dir / "opt.stl"), None
    else:
        report = json.loads((a.run_dir / "rebuild.json").read_text(encoding="utf-8"))
        if report.get("status") != "ok":
            print(f"rebuild failed; nothing to measure: {report.get('reason')}")
            return 1
        mesh = geom.load_mesh(a.run_dir / "final.stl")
    metrics = measure(mesh, cfg, a.h, rebuilt=not a.raw, rebuild_report=report)
    (a.run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps({"pass": metrics["pass"], "summary": metrics["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
