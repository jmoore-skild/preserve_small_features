"""Exclude-and-rebuild post-processing (plan Section 8): final = (opt ∪ keep) ∩ space.

    python -m pipeline.rebuild --run-dir runs/M3_h4 [--geometry-dir geometry] [--config config/runs/M3_h4.yaml]

Writes final.stl (main body), islands.stl, rebuild.json. Hard failures exit non-zero after
writing rebuild.json with status "fail".
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

from pipeline import geom
from pipeline.config import bore_radius, bores, keep_radius, load_config


class RebuildError(RuntimeError):
    """Hard failure: bad inputs, or FIX and LOAD not in one body."""


@dataclass
class RebuildResult:
    final: trimesh.Trimesh
    islands: trimesh.Trimesh | None
    report: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- probes

def keep_probes(cfg: dict) -> dict[str, np.ndarray]:
    """One point per keep zone, mid-wall: radius (r + R)/2 on the +X side, at y = T/2."""
    rho = 0.5 * (bore_radius(cfg) + keep_radius(cfg))
    y = 0.5 * cfg["beam"]["thickness"]
    return {b.name: np.array([b.x + rho, y, b.z]) for b in bores(cfg)}


def classify_components(mesh: trimesh.Trimesh, cfg: dict) -> dict:
    """Split into connected bodies and find the main body (contains every FIX and LOAD probe).

    Returns {"bodies": [trimesh...], "main": index or None, "components": [...], "passive_attached": {...}}.
    """
    bodies = [geom.to_trimesh(c) for c in geom.to_manifold(mesh).decompose()]
    bodies.sort(key=lambda b: -b.volume)
    probes = keep_probes(cfg)
    names = list(probes)
    pts = np.array([probes[n] for n in names])

    components = []
    for i, body in enumerate(bodies):
        inside = body.contains(pts)
        components.append({"index": i, "volume": float(body.volume),
                           "probes": [n for n, hit in zip(names, inside) if hit]})

    required = {b.name for b in bores(cfg) if b.zone in ("FIX", "LOAD")}
    main = next((c["index"] for c in components if required <= set(c["probes"])), None)
    for c in components:
        c["role"] = "main" if c["index"] == main else "island"

    passive = [b.name for b in bores(cfg) if b.zone == "PASSIVE"]
    main_probes = set(components[main]["probes"]) if main is not None else set()
    where = {n: next((c["index"] for c in components if n in c["probes"]), None) for n in names}
    return {
        "bodies": bodies,
        "main": main,
        "components": components,
        "probe_body": where,
        "fix_load_connected": main is not None,
        "passive_attached": {n: n in main_probes for n in passive},
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

def rebuild(opt: trimesh.Trimesh, keep: trimesh.Trimesh, space: trimesh.Trimesh, cfg: dict) -> RebuildResult:
    checks = check_inputs(opt, keep, space, cfg)

    m_opt, m_keep, m_space = (geom.to_manifold(m) for m in (opt, keep, space))
    united = m_opt + m_keep
    final_all = united ^ m_space

    cls = classify_components(geom.to_trimesh(final_all), cfg)
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
        "probe_body": cls["probe_body"],
        "fix_load_connected": cls["fix_load_connected"],
        "passive_attached": cls["passive_attached"],
    }
    if cls["main"] is None:
        report["status"] = "fail"
        report["reason"] = ("FIX and LOAD are not in one body: the rebuild has broken the load path. "
                            f"Probe -> body: {cls['probe_body']}")
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
    ap.add_argument("--geometry-dir", default=Path("geometry"), type=Path)
    ap.add_argument("--config", action="append", default=[], help="override YAML(s) merged onto base.yaml")
    ap.add_argument("--opt", type=Path, help="default: <run-dir>/opt.stl")
    a = ap.parse_args(argv)

    cfg = load_config(*a.config)
    opt = geom.load_mesh(a.opt or a.run_dir / "opt.stl")
    keep = geom.load_mesh(a.geometry_dir / "keep.stl")
    space = geom.load_mesh(a.geometry_dir / "space.stl")

    out = a.run_dir / "rebuild.json"
    try:
        res = rebuild(opt, keep, space, cfg)
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
