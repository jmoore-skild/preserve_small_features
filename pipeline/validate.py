"""Validation solve (plan Section 9): rebuild against a remeshed SPACE, tet-mesh with TetGen, and solve
a linear static in PyMAPDL.

    python -m pipeline.validate --run-dir runs/M3_h4                 # validates final.stl
    python -m pipeline.validate --run-dir runs/M0_h4 --raw           # raw M0 opt.stl (only if it has bores)

BCs match the TO model: all DOF fixed on the FIX bore-surface nodes; 200 N in -Z at a pilot node on the
LOAD bore centroid, distributed to the LOAD bore-surface nodes by RBE3. Quadratic tets (SOLID187).
Refines (global and bore size halved) until pilot UZ changes by less than validate.convergence_tol.
Writes validation.json in the run directory.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
import pyvista as pv
import tetgen
import trimesh

from pipeline import geom
from pipeline.config import bore_radius, bores, keep_radius, load_config
from pipeline.run_mechanical import zone_x_range

MAX_LEVELS = 3
REMESH_ATTEMPTS = 5


# --------------------------------------------------------------------------- mesh

def radial_to_axes(points: np.ndarray, cfg: dict, zone: str | None = None) -> np.ndarray:
    """Distance from each point to the nearest bore axis (axes parallel to Y), optionally one zone only."""
    axes = np.array([(b.x, b.z) for b in bores(cfg) if zone is None or b.zone == zone])
    d = np.hypot(points[:, None, 0] - axes[None, :, 0], points[:, None, 2] - axes[None, :, 1])
    return d.min(axis=1)


def remesh_space(space: trimesh.Trimesh, cfg: dict, global_size: float, bore_size: float) -> trimesh.Trimesh:
    """remesh_space_once with retries: pymeshlab's result varies between calls in one process, and an
    occasional result self-intersects (caught by the check), so vary the iteration count and retry."""
    errors = []
    for attempt in range(REMESH_ATTEMPTS):
        try:
            return remesh_space_once(space, cfg, global_size, bore_size, extra_iterations=attempt)
        except ValueError as e:
            errors.append(str(e))
    raise ValueError(f"remeshing SPACE at {global_size}/{bore_size} mm failed: {errors}")


def remesh_space_once(space: trimesh.Trimesh, cfg: dict, global_size: float, bore_size: float,
                      extra_iterations: int = 0) -> trimesh.Trimesh:
    """Isotropic remesh of SPACE (pymeshlab): global_size everywhere, then bore_size within the refinement
    radius of each bore axis. Feature edges (box edges, bore rims) are kept and vertices stay within
    surface_tolerance of the input.

    Onshape's STL triangulates the flat faces as long slivers fanning out from the bore circles, which
    makes TetGen's quality refinement explode. Only SPACE is remeshed: it has no thin walls, whereas
    remeshing the level-set result (or final.stl) introduced self-intersections in thin members.
    """
    import pymeshlab as ml

    vc = cfg["validate"]
    ms = ml.MeshSet()
    ms.add_mesh(ml.Mesh(np.asarray(space.vertices, dtype=np.float64), np.asarray(space.faces)))
    common = dict(featuredeg=vc["feature_angle"], maxsurfdist=ml.PureValue(vc["surface_tolerance"]),
                  iterations=vc["remesh_iterations"] + extra_iterations)
    ms.meshing_isotropic_explicit_remeshing(targetlen=ml.PureValue(global_size), **common)
    rr = (vc["bore_refine_radius_factor"] * keep_radius(cfg)) ** 2
    cond = " || ".join(f"((x0-{b.x})^2+(z0-{b.z})^2 < {rr})" for b in bores(cfg))
    ms.compute_selection_by_condition_per_face(condselect=cond)
    ms.meshing_isotropic_explicit_remeshing(targetlen=ml.PureValue(bore_size), selectedonly=True, **common)
    ms.compute_selection_by_self_intersections_per_face()
    if ms.current_mesh().selected_face_number():
        raise ValueError(f"remeshed SPACE self-intersects ({ms.current_mesh().selected_face_number()} faces)")
    m = ms.current_mesh()
    out = trimesh.Trimesh(m.vertex_matrix(), m.face_matrix(), process=False)
    if not out.is_volume:
        raise ValueError("remeshed SPACE is not a closed volume")
    return out


def fe_surface(opt, keep, space, cfg: dict, global_size: float, bore_size: float) -> trimesh.Trimesh:
    """The validation surface: the same rebuild, (opt ∪ keep) ∩ space, against a remeshed SPACE.
    Same geometry as final.stl (to surface_tolerance), better-shaped triangles for meshing."""
    from pipeline.rebuild import rebuild
    return rebuild(opt, keep, remesh_space(space, cfg, global_size, bore_size), cfg).final


def tet_mesh(surface: trimesh.Trimesh, cfg: dict, global_size: float) -> pv.UnstructuredGrid:
    """Quadratic tets (TetGen): sizes grade in from the surface triangulation, with tet volume capped at
    tet_volume_cap * global_size^3. The cap goes in as a raw switch string: this tetgen build ignores the
    maxvolume keyword, and segfaults when given a background sizing mesh.

    TetGen runs in a child process killed after tet_timeout seconds: on some remeshes it never returns
    (once 74 min on the full SPACE body). A timeout or crash raises RuntimeError, like a TetGen failure,
    so the caller retries on a fresh remesh."""
    import multiprocessing as mp

    vc = cfg["validate"]
    switches = f"pq{vc['tet_radius_edge']}a{vc['tet_volume_cap'] * global_size ** 3:.6g}o2Q"
    with tempfile.TemporaryDirectory(prefix="tetgen_") as tmp:
        src, dst = Path(tmp) / "surface.npz", Path(tmp) / "grid.npz"
        np.savez(src, vertices=np.asarray(surface.vertices), faces=np.asarray(surface.faces))
        proc = mp.get_context("spawn").Process(target=_tet_mesh_child, args=(str(src), str(dst), switches))
        proc.start()
        proc.join(vc["tet_timeout"])
        if proc.is_alive():
            proc.kill()
            proc.join()
            raise RuntimeError(f"TetGen timed out after {vc['tet_timeout']} s")
        if proc.exitcode != 0 or not dst.exists():
            raise RuntimeError(f"TetGen child exited with code {proc.exitcode}")
        with np.load(dst) as d:
            return pv.UnstructuredGrid(d["cells"], d["celltypes"], d["points"])


def _tet_mesh_child(src: str, dst: str, switches: str) -> None:
    """tet_mesh's child process: surface arrays in, grid arrays out (npz, no VTK file I/O)."""
    with np.load(src) as d:
        v, f = d["vertices"], d["faces"]
    tet = tetgen.TetGen(pv.PolyData(v, np.hstack([np.full((len(f), 1), 3), f]).ravel()))
    tet.tetrahedralize(switches=switches)
    g = tet.grid
    np.savez(dst, cells=np.asarray(g.cells), celltypes=np.asarray(g.celltypes), points=np.asarray(g.points))


# --------------------------------------------------------------------------- solve

def solve(grid: pv.UnstructuredGrid, cfg: dict, mapdl, work: Path) -> dict:
    """Linear static on a quadratic tet grid. Returns pilot UZ, node arrays and nodal von Mises."""
    vc, mc = cfg["validate"], cfg["material"]
    r = bore_radius(cfg)
    pts = np.asarray(grid.points)

    def bore_nodes(zone):
        lo, hi = zone_x_range(cfg, zone)
        near = np.abs(radial_to_axes(pts, cfg, zone) - r) <= vc["fix_radial_tol"]
        return near & (pts[:, 0] >= lo) & (pts[:, 0] <= hi)

    fix, load = bore_nodes("FIX"), bore_nodes("LOAD")
    if not fix.any() or not load.any():
        raise ValueError(f"no bore-surface nodes found (FIX {fix.sum()}, LOAD {load.sum()}): "
                         "the mesh has no bores to carry the BCs")
    grid = grid.copy()
    grid.point_data["FIXNODES"] = fix
    grid.point_data["LOADNODES"] = load
    ones = np.ones(grid.n_cells, dtype=np.int32)
    grid.cell_data["ansys_material_type"] = ones       # save_as_archive otherwise numbers these per cell
    grid.cell_data["ansys_real_constant"] = ones
    grid.cell_data["ansys_etype"] = ones
    grid.cell_data["ansys_elem_type_num"] = np.full(grid.n_cells, 187, dtype=np.int32)

    from ansys.mapdl.reader import save_as_archive
    cdb = work / "model.cdb"
    save_as_archive(str(cdb), grid)

    lc = cfg["loads"]["remote_force"]
    mapdl.clear()
    mapdl.prep7()
    mapdl.cdread("DB", str(cdb))
    mapdl.mp("EX", 1, mc["youngs_modulus"])           # MPa
    mapdl.mp("PRXY", 1, mc["poisson_ratio"])
    mapdl.mp("DENS", 1, mc["density"] * 1e-12)         # kg/m^3 -> tonne/mm^3
    pilot = len(pts) + 1
    mapdl.n(pilot, *vc["pilot"])
    et = int(mapdl.get_value("ETYP", 0, "NUM", "MAX")) + 1     # the CDB already defines some types
    rl = int(mapdl.get_value("RCON", 0, "NUM", "MAX")) + 1
    mapdl.et(et, "MASS21")
    mapdl.keyopt(et, 3, 2)                             # 3D mass, no rotary inertia: UX/UY/UZ at the pilot
    mapdl.r(rl, 0)
    mapdl.type(et)
    mapdl.real(rl)
    mapdl.e(pilot)
    mapdl.rbe3(pilot, "UXYZ", "LOADNODES")
    mapdl.cmsel("S", "FIXNODES")
    mapdl.d("ALL", "ALL")
    mapdl.allsel()
    for comp, d in zip(("FX", "FY", "FZ"), lc["direction"]):
        if d:
            mapdl.f(pilot, comp, lc["magnitude"] * d)
    mapdl.finish()
    mapdl.slashsolu()
    mapdl.antype("STATIC")
    mapdl.solve()
    mapdl.finish()
    mapdl.post1()
    mapdl.set(1, 1)
    uz = mapdl.get_value("NODE", pilot, "U", "Z")
    nnum = mapdl.mesh.nnum
    seqv = mapdl.post_processing.nodal_eqv_stress()
    vm = np.full(len(pts), np.nan)
    keep = nnum <= len(pts)
    vm[nnum[keep] - 1] = seqv[keep]
    mapdl.finish()
    return {"uz": float(uz), "vm": vm, "n_fix": int(fix.sum()), "n_load": int(load.sum())}


# --------------------------------------------------------------------------- stresses

def tet_volume(grid: pv.UnstructuredGrid) -> float:
    """Volume from the corner nodes (no VTK filters: Application Control blocks vtkFiltersVerdict here)."""
    c = np.asarray(grid.cells_dict[pv.CellType.QUADRATIC_TETRA])[:, :4]
    p = np.asarray(grid.points)
    a, b, cc, d = (p[c[:, i]] for i in range(4))
    return float(np.abs(np.einsum("ij,ij->i", b - a, np.cross(cc - a, d - a))).sum() / 6)


def stress_report(grid: pv.UnstructuredGrid, vm: np.ndarray, cfg: dict) -> dict:
    vc = cfg["validate"]
    r, R = bore_radius(cfg), keep_radius(cfg)
    pts = np.asarray(grid.points)
    rho = radial_to_axes(pts, cfg)

    members = rho > R + vc["exclude_near_features"]
    out = {"member_vm_max": float(np.nanmax(vm[members])) if members.any() else None, "bores": {}}

    shell = vc["seam_shell_halfwidth"]
    for b in bores(cfg):
        rb = np.hypot(pts[:, 0] - b.x, pts[:, 2] - b.z)
        on_bore = np.abs(rb - r) <= vc["fix_radial_tol"]
        # Seam: nodes in a thin shell at a fixed offset outside the keep boundary (creases are singular).
        seam = np.abs(rb - (R + vc["seam_sample_offset"])) <= shell
        out["bores"][b.name] = {
            "bore_vm_max": float(np.nanmax(vm[on_bore])) if on_bore.any() else None,
            "seam_vm_max": float(np.nanmax(vm[seam])) if seam.any() else None,
            "seam_nodes": int(seam.sum()),
        }
    return out


# --------------------------------------------------------------------------- driver

def validate(make_surface, reference: trimesh.Trimesh, cfg: dict, mapdl, work: Path) -> dict:
    """make_surface(global_size, bore_size) -> surface to tet-mesh at that level. Mass and volume come
    from `reference` (final.stl), the part that would be printed."""
    vc = cfg["validate"]
    lc = cfg["loads"]["remote_force"]
    F = lc["magnitude"]
    levels, prev = [], None
    g, b = vc["global_size"], vc["bore_size"]
    for level in range(MAX_LEVELS):
        t0 = time.perf_counter()
        # TetGen occasionally fails on a given remesh (split_subface / recoversubfaces); the remesh
        # differs between calls, so retry both.
        errors = []
        for attempt in range(REMESH_ATTEMPTS):
            surface = make_surface(g, b)
            try:
                grid = tet_mesh(surface, cfg, g)
                break
            except RuntimeError as e:
                errors.append(str(e))
        else:
            raise ValueError(f"TetGen failed at {g}/{b} mm on {REMESH_ATTEMPTS} remeshes: {errors}")
        t1 = time.perf_counter()
        res = solve(grid, cfg, mapdl, work)
        t2 = time.perf_counter()
        rec = {"level": level, "global_size": g, "bore_size": b, "nodes": int(grid.n_points),
               "elements": int(grid.n_cells), "tet_volume": tet_volume(grid),
               "fe_surface_volume": float(surface.volume), "mesh_attempts": len(errors) + 1,
               "pilot_uz": res["uz"],
               "n_fix_nodes": res["n_fix"], "n_load_nodes": res["n_load"],
               "mesh_time": t1 - t0, "solve_time": t2 - t1}
        if prev is not None:
            rec["uz_change"] = abs(res["uz"] - prev) / abs(prev)
        levels.append(rec)
        print(f"level {level}: h {g:g}/{b:g} mm, {grid.n_cells} tets, pilot UZ {res['uz']:.6f} mm"
              + (f", change {rec['uz_change']:.2%}" if prev is not None else ""), flush=True)
        if prev is not None and rec["uz_change"] < vc["convergence_tol"]:
            break
        prev = res["uz"]
        g, b = g / 2, b / 2

    uz = levels[-1]["pilot_uz"]
    volume = float(reference.volume)
    mass = volume * cfg["material"]["density"] * 1e-9      # mm^3 * kg/m^3 -> kg
    compliance = F * abs(uz)                                # F*u, N*mm
    return {
        "status": "ok",
        "converged": len(levels) > 1 and levels[-1]["uz_change"] < vc["convergence_tol"],
        "levels": levels,
        "pilot_uz": uz,
        "compliance": compliance,
        "strain_energy": 0.5 * compliance,                  # comparable to Mechanical's TO "compliance"
        "stiffness": F / abs(uz),                           # N/mm
        "volume": volume,
        "mass": mass,
        "specific_stiffness": F / abs(uz) / mass,           # N/mm/kg
        "stress": stress_report(grid, res["vm"], cfg),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--geometry-dir", default=Path("geometry"), type=Path)
    ap.add_argument("--raw", action="store_true", help="validate opt.stl as-is (raw M0), not the rebuild")
    ap.add_argument("--config", action="append", default=[])
    ap.add_argument("--nproc", type=int, default=4)
    a = ap.parse_args(argv)
    cfg = load_config(*a.config)
    opt = geom.load_mesh(a.run_dir / "opt.stl")
    if a.raw:
        reference, mesh_name = opt, "opt.stl"
        make_surface = lambda g, b: opt                                     # noqa: E731
    else:
        reference, mesh_name = geom.load_mesh(a.run_dir / "final.stl"), "final.stl"
        keep = geom.load_mesh(a.geometry_dir / "keep.stl")
        space = geom.load_mesh(a.geometry_dir / "space.stl")
        make_surface = lambda g, b: fe_surface(opt, keep, space, cfg, g, b)  # noqa: E731
    out = a.run_dir / ("validation_raw.json" if a.raw else "validation.json")

    from ansys.mapdl.core import launch_mapdl
    work = Path(tempfile.mkdtemp(prefix="validate_"))
    mapdl = launch_mapdl(version=cfg["mechanical"]["version"], nproc=a.nproc, run_location=str(work),
                         override=True)
    try:
        report = validate(make_surface, reference, cfg, mapdl, work)
    except ValueError as e:
        report = {"status": "not_validated", "reason": str(e)}
    finally:
        report_version = str(mapdl.version)
        mapdl.exit()
        shutil.rmtree(work, ignore_errors=True)
    report.update(mesh=mesh_name, ansys_version=report_version)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in ("status", "reason", "converged", "pilot_uz", "compliance",
                                                 "strain_energy", "mass", "specific_stiffness")}, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
