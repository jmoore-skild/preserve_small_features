"""Exclude-and-rebuild post-processing (plan Section 8): final = (opt ∪ keep) ∩ space.

    python -m pipeline.rebuild --run-dir runs/<part>/<id>

Reads the run's config.yaml (for the part), its opt.stl, and geometry/<part>/. Writes final.stl (main body),
islands.stl, rebuild.json. Hard failures exit non-zero after writing rebuild.json with status "fail".
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

from pipeline import geom, regions as rg
from pipeline.config import geometry_dir, read_run_config


# Booleans can leave zero-volume sliver components (bracket M3_h2: four of them); they are dropped, and counted.
DEGENERATE_VOLUME = 1e-3   # mm^3


class RebuildError(RuntimeError):
    """Hard failure: bad inputs, or FIX and LOAD not in one body."""


@dataclass
class RebuildResult:
    final: trimesh.Trimesh
    islands: trimesh.Trimesh | None
    report: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- components

def classify_components(mesh: trimesh.Trimesh, geo: rg.PartGeometry) -> dict:
    """Split into connected bodies and find the main body, the one holding every FIX and LOAD region.

    A region belongs to the body that holds most of its overlap volume K ∩ SPACE (none if no body reaches it).
    Returns {"bodies": [trimesh...], "main": index or None, "components": [...], "region_body": {...},
    "fix_load_connected": bool, "passive_attached": {...}}.
    """
    parts = geom.to_manifold(mesh).decompose()
    bodies = sorted((b for b in parts if b.volume() > DEGENERATE_VOLUME), key=lambda b: -b.volume())
    m_space = geom.to_manifold(geo.space)
    cores = {r.name: geom.to_manifold(geo.region_keep(r.name)) ^ m_space for r in geo.regions}

    def box(m):
        bb = np.array(m.bounding_box())
        return bb[:3], bb[3:]

    overlap = np.zeros((len(bodies), len(cores)))
    for i, body in enumerate(bodies):
        lo, hi = box(body)
        for j, core in enumerate(cores.values()):
            clo, chi = box(core)
            if np.all(hi >= clo) and np.all(chi >= lo):
                overlap[i, j] = (body ^ core).volume()
    names = list(cores)
    region_body = {n: (int(np.argmax(overlap[:, j])) if overlap[:, j].max() > 0 else None)
                   for j, n in enumerate(names)}

    components = [{"index": i, "volume": float(b.volume()),
                   "regions": [n for n in names if region_body[n] == i]} for i, b in enumerate(bodies)]
    required = {r.name for r in geo.regions if r.role in ("FIX", "LOAD")}
    main = next((c["index"] for c in components if required <= set(c["regions"])), None)
    for c in components:
        c["role"] = "main" if c["index"] == main else "island"
    return {
        "bodies": [geom.to_trimesh(b) for b in bodies],
        "main": main,
        "components": components,
        "region_body": region_body,
        "region_overlap_fraction": {n: float(overlap[region_body[n], j] / cores[n].volume())
                                    if region_body[n] is not None else 0.0 for j, n in enumerate(names)},
        "fix_load_connected": main is not None,
        "passive_attached": {r.name: main is not None and region_body[r.name] == main
                             for r in geo.of_role("PASSIVE")},
        "degenerate_dropped": len(parts) - len(bodies),
    }


# --------------------------------------------------------------------------- input checks

def check_inputs(opt: trimesh.Trimesh, keep: trimesh.Trimesh, space: trimesh.Trimesh, cfg: dict) -> dict:
    rc = cfg["rebuild"]
    for name, m in (("opt", opt), ("keep", keep), ("space", space)):
        if not m.is_volume:
            raise RebuildError(
                f"input check: {name} is not a closed, consistently oriented volume "
                f"(watertight={m.is_watertight}, winding_consistent={m.is_winding_consistent}). "
                "No automatic repair is applied.")

    ratio = opt.volume / space.volume
    lo, hi = rc["opt_space_volume_ratio"]
    if not lo < ratio < hi:
        raise RebuildError(
            f"input check: volume ratio opt/space = {ratio:.4g}, expected in ({lo}, {hi}). "
            "Likely a unit mismatch (m vs mm) or the wrong file.")

    # Signed distance (positive inside) rather than contains(): much of opt's surface lies exactly
    # on space's faces, where a ray-parity test is ambiguous.
    sub = opt.vertices[:: rc["inside_space_subsample"]]
    with np.errstate(divide="ignore", invalid="ignore"):   # degenerate slivers are harmless here
        sd = trimesh.proximity.signed_distance(space, sub)
    frac = float(np.mean(sd >= -rc["inside_space_tolerance"]))
    if frac < rc["inside_space_min_fraction"]:
        raise RebuildError(
            f"input check: only {frac:.1%} of sampled opt vertices lie inside space "
            f"(tolerance {rc['inside_space_tolerance']} mm, need ≥ {rc['inside_space_min_fraction']:.0%}). "
            "Likely a frame or unit mismatch.")
    return {"opt_space_volume_ratio": ratio, "opt_inside_space_fraction": frac}


# --------------------------------------------------------------------------- rebuild

def rebuild(opt: trimesh.Trimesh, geo: rg.PartGeometry, cfg: dict) -> RebuildResult:
    checks = check_inputs(opt, geo.keep, geo.space, cfg)

    m_opt, m_keep, m_space = (geom.to_manifold(m) for m in (opt, geo.keep, geo.space))
    united = m_opt + m_keep
    final_all = united ^ m_space

    cls = classify_components(geom.to_trimesh(final_all), geo)
    v_union, v_all = united.volume(), final_all.volume()
    report = {
        "status": "ok",
        "input_checks": checks,
        "volumes": {
            "opt": m_opt.volume(),
            "added": v_union - m_opt.volume(),
            "trimmed": v_union - v_all,
            "final_all": v_all,
            "final": None,
            "islands": None,
        },
        "components": cls["components"],
        "region_body": cls["region_body"],
        "region_overlap_fraction": cls["region_overlap_fraction"],
        "degenerate_components_dropped": cls["degenerate_dropped"],
        "fix_load_connected": cls["fix_load_connected"],
        "passive_attached": cls["passive_attached"],
    }
    if cls["main"] is None:
        report["status"] = "fail"
        report["reason"] = ("FIX and LOAD are not in one body: the rebuild has broken the load path. "
                            f"Region -> body: {cls['region_body']}")
        raise RebuildError(report["reason"], report)

    bodies = cls["bodies"]
    final = bodies[cls["main"]]
    others = [b for i, b in enumerate(bodies) if i != cls["main"]]
    islands = trimesh.util.concatenate(others) if others else None
    report["volumes"]["final"] = float(final.volume)
    report["volumes"]["islands"] = float(sum(b.volume for b in others))
    return RebuildResult(final, islands, report)


# --------------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--geometry-dir", type=Path, help="default: geometry/<part of the run>")
    ap.add_argument("--opt", type=Path, help="default: <run-dir>/opt.stl")
    a = ap.parse_args(argv)

    cfg = read_run_config(a.run_dir)
    opt = geom.load_mesh(a.opt or a.run_dir / "opt.stl")
    geo = rg.load_part_geometry(a.geometry_dir or geometry_dir(cfg["part"]))

    out = a.run_dir / "rebuild.json"
    try:
        res = rebuild(opt, geo, cfg)
    except RebuildError as e:
        report = e.args[1] if len(e.args) > 1 else {"status": "fail", "reason": str(e)}
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"REBUILD FAILED: {e.args[0]}", file=sys.stderr)
        return 1

    res.final.export(a.run_dir / "final.stl")
    if not geom.load_mesh(a.run_dir / "final.stl").is_volume:
        res.report.update(status="fail", reason="final.stl does not reload as a closed volume (float32 STL round trip)")
        out.write_text(json.dumps(res.report, indent=2), encoding="utf-8")
        print(f"REBUILD FAILED: {res.report['reason']}", file=sys.stderr)
        return 1
    if res.islands is not None:
        res.islands.export(a.run_dir / "islands.stl")
    out.write_text(json.dumps(res.report, indent=2), encoding="utf-8")
    v = res.report["volumes"]
    print(f"final {v['final']:.1f} mm^3, islands {v['islands']:.1f} mm^3, "
          f"added {v['added']:.1f}, trimmed {v['trimmed']:.1f}; passive attached: {res.report['passive_attached']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
