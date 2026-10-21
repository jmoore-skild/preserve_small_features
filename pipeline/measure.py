"""Geometry metrics (plan Phase 6 and Section 9): preserved-surface deviation, connectivity, seam necks, volumes.

    python -m pipeline.measure --run-dir runs/<part>/M3_h4               # measures final.stl (rebuilt)
    python -m pipeline.measure --run-dir runs/<part>/M0_h4 --raw         # measures opt.stl, report-only

The metrics work on any part's regions. (The Phase 4 beam-only bore-fit, washer-seat and seam-shell metrics were
retired at Gate 4, 2026-10-21; runs/_phase4 holds their results.) Writes metrics.json in the run directory.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import trimesh

from pipeline import geom, regions as rg
from pipeline.config import geometry_dir, read_run_config
from pipeline.rebuild import classify_components


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

def measure(mesh: trimesh.Trimesh, geo: rg.PartGeometry, cfg: dict, h: float, rebuilt: bool = True,
            rebuild_report: dict | None = None) -> dict:
    """All geometry metrics. For raw (non-rebuilt) meshes, fidelity results are report-only."""
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
