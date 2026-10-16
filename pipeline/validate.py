"""Validation solve (plan Section 9): Ansys Prime wraps the STL and meshes quadratic tets, and PyMAPDL
solves a linear static.

    python -m pipeline.validate --run-dir runs/M3_h4                 # validates final.stl
    python -m pipeline.validate --run-dir runs/M0_h4 --raw           # raw M0 opt.stl (only if it has bores)

BCs match the TO model: all DOF fixed on the FIX bore-surface nodes; 200 N in -Z at a pilot node on the
LOAD bore centroid, distributed to the LOAD bore-surface nodes by RBE3. Quadratic tets (SOLID187).
Refines (global and bore size halved) until pilot UZ changes by less than validate.convergence_tol.
Writes validation.json in the run directory.

Meshing goes through Prime's wrapper because the STLs are hard to mesh as given: Onshape's flat faces are
sliver fans, and the rebuild's booleans leave slivers and self-intersections in thin members. TetGen, gmsh,
and Prime meshing the surface as given each crashed, hung, or left near-flat tets on them.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

from pipeline import geom
from pipeline.config import bore_radius, bores, keep_radius, load_config
from pipeline.run_mechanical import zone_x_range

MAX_LEVELS = 3


# --------------------------------------------------------------------------- mesh

def radial_to_axes(points: np.ndarray, cfg: dict, zone: str | None = None) -> np.ndarray:
    """Distance from each point to the nearest bore axis (axes parallel to Y), optionally one zone only."""
    axes = np.array([(b.x, b.z) for b in bores(cfg) if zone is None or b.zone == zone])
    d = np.hypot(points[:, None, 0] - axes[None, :, 0], points[:, None, 2] - axes[None, :, 1])
    return d.min(axis=1)


@dataclass
class FeMesh:
    """A Prime CDB and the arrays read back from it. No VTK grid: Smart App Control blocks VTK DLLs here."""
    cdb: Path
    points: np.ndarray        # (n, 3); row i is node nnum[i]
    nnum: np.ndarray
    tets: np.ndarray          # (e, 4) corner-node rows into points
    used: np.ndarray          # nodes referenced by an element (Prime also writes unreferenced wrap nodes)
    materials: np.ndarray

    @property
    def n_elements(self) -> int:
        return len(self.tets)

    def volume(self) -> float:
        """Volume from the corner nodes."""
        a, b, c, d = (self.points[self.tets[:, i]] for i in range(4))
        return float(np.abs(np.einsum("ij,ij->i", b - a, np.cross(c - a, d - a))).sum() / 6)


def read_cdb(cdb: Path) -> FeMesh:
    from ansys.mapdl.reader import Archive
    arch = Archive(str(cdb), parse_vtk=False)
    elem = np.asarray(arch.elem)                       # 10 header fields (MAT first), then 10 node numbers
    row = np.zeros(arch.nnum.max() + 1, dtype=np.int64)
    row[arch.nnum] = np.arange(len(arch.nnum))
    used = np.zeros(len(arch.nnum), dtype=bool)
    used[row[elem[:, 10:20].ravel()]] = True
    return FeMesh(cdb, np.asarray(arch.nodes, dtype=float), np.asarray(arch.nnum), row[elem[:, 10:14]], used,
                  np.unique(elem[:, 0]))


def prime_mesh(stl: Path, cfg: dict, global_size: float, bore_size: float, cdb: Path) -> FeMesh:
    """Wrap `stl` in Prime (curvature sizing between bore_size and global_size, feature edges recovered),
    check that the wrap is free of self-intersections, fill it with quadratic tets, and export a CDB.
    Each mesh gets a fresh Prime session, so levels don't share state."""
    import ansys.meshing.prime as prime
    from ansys.meshing.prime import lucid

    vc = cfg["validate"]
    root = os.path.join(os.environ[f"AWP_ROOT{cfg['mechanical']['version']}"], "meshing", "Prime")
    client = prime.launch_prime(prime_root=root, timeout=180)
    try:
        model = client.model
        mesh = lucid.Mesh(model)
        mesh.read(str(stl))
        mesh.wrap(min_size=bore_size, max_size=global_size, growth_rate=vc["growth_rate"],
                  elements_per_gap=vc["wrap_elements_per_gap"], feature_angle=vc["feature_angle"])
        part = model.parts[0]
        found = prime.SurfaceSearch(model).search_zonelets_by_self_intersections(
            part.id, part.get_face_zonelets(), 1, prime.SearchBySelfIntersectionParams(model)).n_found
        if found:
            raise ValueError(f"Prime wrap of {stl.name} at {global_size}/{bore_size} mm self-intersects "
                             f"({found} faces)")
        model.set_global_sizing_params(prime.GlobalSizingParams(model, min=bore_size, max=global_size,
                                                                growth_rate=vc["growth_rate"]))
        mesh.volume_mesh(volume_fill_type=prime.VolumeFillType.TET, quadratic=True)
        mesh.write(str(cdb))
    finally:
        client.exit()
    fe = read_cdb(cdb)
    if not fe.n_elements:
        raise ValueError(f"Prime produced no tets for {stl.name} at {global_size}/{bore_size} mm")
    return fe


def add_node_components(cdb: Path, components: dict[str, np.ndarray]) -> None:
    """Insert node CMBLOCKs before the CDB's closing FINISH (MAPDL reads CMBLOCK only inside CDREAD)."""
    with open(cdb, "r+b") as f:
        f.seek(-64, os.SEEK_END)
        tail = f.read()
        cut = tail.rfind(b"FINISH")
        if cut < 0:
            raise ValueError(f"{cdb.name} does not end with FINISH")
        f.seek(cut - len(tail), os.SEEK_END)
        f.truncate()
        for name, nodes in components.items():
            f.write(f"CMBLOCK,{name},NODE,{len(nodes):10d}\n(8i10)\n".encode())
            for i in range(0, len(nodes), 8):
                f.write("".join(f"{n:10d}" for n in nodes[i:i + 8]).encode() + b"\n")
        f.write(b"FINISH\n")


# --------------------------------------------------------------------------- solve

def solve(fe: FeMesh, cfg: dict, mapdl) -> dict:
    """Linear static on a Prime quadratic tet mesh. Returns pilot UZ and nodal von Mises (by row)."""
    vc, mc = cfg["validate"], cfg["material"]
    r = bore_radius(cfg)
    pts = fe.points

    def bore_nodes(zone):
        lo, hi = zone_x_range(cfg, zone)
        near = np.abs(radial_to_axes(pts, cfg, zone) - r) <= vc["fix_radial_tol"]
        return near & (pts[:, 0] >= lo) & (pts[:, 0] <= hi) & fe.used

    fix, load = bore_nodes("FIX"), bore_nodes("LOAD")
    if not fix.any() or not load.any():
        raise ValueError(f"no bore-surface nodes found (FIX {fix.sum()}, LOAD {load.sum()}): "
                         "the mesh has no bores to carry the BCs")
    add_node_components(fe.cdb, {"FIXNODES": fe.nnum[fix], "LOADNODES": fe.nnum[load]})

    lc = cfg["loads"]["remote_force"]
    mapdl.clear()
    mapdl.prep7()
    mapdl.cdread("DB", str(fe.cdb))
    mapdl.prep7()                                      # the CDB ends with FINISH
    for mat in fe.materials:
        mapdl.mp("EX", mat, mc["youngs_modulus"])     # MPa
        mapdl.mp("PRXY", mat, mc["poisson_ratio"])
        mapdl.mp("DENS", mat, mc["density"] * 1e-12)  # kg/m^3 -> tonne/mm^3
    pilot = int(fe.nnum.max()) + 1
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
    mapdl.eqslv(vc["solver"], vc["solver_tol"])
    mapdl.solve()
    mapdl.finish()
    mapdl.post1()
    mapdl.set(1, 1)
    uz = mapdl.get_value("NODE", pilot, "U", "Z")
    nnum = np.asarray(mapdl.mesh.nnum)
    seqv = mapdl.post_processing.nodal_eqv_stress()
    row = np.full(max(int(nnum.max()), pilot) + 1, -1, dtype=np.int64)
    row[fe.nnum] = np.arange(len(fe.nnum))
    vm = np.full(len(pts), np.nan)
    hit = row[nnum] >= 0
    vm[row[nnum[hit]]] = seqv[hit]
    mapdl.finish()
    return {"uz": float(uz), "vm": vm, "n_fix": int(fix.sum()), "n_load": int(load.sum())}


# --------------------------------------------------------------------------- stresses

def stress_report(fe: FeMesh, vm: np.ndarray, cfg: dict) -> dict:
    vc = cfg["validate"]
    r, R = bore_radius(cfg), keep_radius(cfg)
    pts = fe.points
    rho = radial_to_axes(pts, cfg)

    members = (rho > R + vc["exclude_near_features"]) & fe.used
    out = {"member_vm_max": float(np.nanmax(vm[members])) if members.any() else None, "bores": {}}

    shell = vc["seam_shell_halfwidth"]
    for b in bores(cfg):
        rb = np.hypot(pts[:, 0] - b.x, pts[:, 2] - b.z)
        on_bore = (np.abs(rb - r) <= vc["fix_radial_tol"]) & fe.used
        # Seam: nodes in a thin shell at a fixed offset outside the keep boundary (creases are singular).
        seam = (np.abs(rb - (R + vc["seam_sample_offset"])) <= shell) & fe.used
        out["bores"][b.name] = {
            "bore_vm_max": float(np.nanmax(vm[on_bore])) if on_bore.any() else None,
            "seam_vm_max": float(np.nanmax(vm[seam])) if seam.any() else None,
            "seam_nodes": int(seam.sum()),
        }
    return out


# --------------------------------------------------------------------------- driver

def validate(stl: Path, reference: trimesh.Trimesh, cfg: dict, work: Path, nprocs: list[int] | None = None
             ) -> dict:
    """Mesh `stl` at successively halved sizes and solve until pilot UZ converges. Mass and volume come
    from `reference` (the stl as loaded), the part that would be printed.

    Prime and MAPDL check out the same base license (`ansys`) and the pool grants one seat, so each level
    meshes with Prime closed before MAPDL starts, and MAPDL exits before the next level meshes."""
    vc = cfg["validate"]
    lc = cfg["loads"]["remote_force"]
    F = lc["magnitude"]
    levels, prev = [], None
    g, b = vc["global_size"], vc["bore_size"]
    for level in range(MAX_LEVELS):
        t0 = time.perf_counter()
        fe = prime_mesh(stl, cfg, g, b, work / f"level{level}.cdb")
        t1 = time.perf_counter()
        mapdl, nproc = launch(cfg, work, nprocs)
        try:
            ansys_version = str(mapdl.version)
            res = solve(fe, cfg, mapdl)
        finally:
            mapdl.exit()
        t2 = time.perf_counter()
        tet_volume = fe.volume()
        rec = {"level": level, "global_size": g, "bore_size": b, "nodes": int(fe.used.sum()),
               "elements": fe.n_elements, "tet_volume": tet_volume,
               "volume_error": tet_volume / float(reference.volume) - 1,
               "pilot_uz": res["uz"],
               "n_fix_nodes": res["n_fix"], "n_load_nodes": res["n_load"], "nproc": nproc,
               "mesh_time": t1 - t0, "solve_time": t2 - t1}
        if prev is not None:
            rec["uz_change"] = abs(res["uz"] - prev) / abs(prev)
        levels.append(rec)
        print(f"level {level}: h {g:g}/{b:g} mm, {fe.n_elements} tets, pilot UZ {res['uz']:.6f} mm"
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
        "stress": stress_report(fe, res["vm"], cfg),
        "ansys_version": ansys_version,
    }


def launch(cfg: dict, work: Path, nprocs: list[int] | None = None):
    """(mapdl, nproc): MAPDL started with the first process count in `nprocs` (default validate.nproc)
    whose licenses are granted. Above 4 cores each step needs another HPC pack from the shared pool; a
    denied checkout kills MAPDL at startup, which launch_mapdl reports as a LaunchError after its
    connection timeout."""
    from ansys.mapdl.core import launch_mapdl
    from ansys.mapdl.core.launcher.errors import LaunchError

    errors = []
    for n in nprocs or cfg["validate"]["nproc"]:
        try:
            mapdl = launch_mapdl(version=cfg["mechanical"]["version"], nproc=n, run_location=str(work),
                                 override=True)
        except LaunchError as e:
            errors.append(f"{n}: {e}")
            print(f"MAPDL with {n} processes failed to start (HPC licenses not granted?), trying fewer",
                  flush=True)
            continue
        print(f"MAPDL started with {n} processes", flush=True)
        return mapdl, n
    raise LaunchError(f"MAPDL failed to start at every process count: {errors}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--raw", action="store_true", help="validate opt.stl as-is (raw M0), not the rebuild")
    ap.add_argument("--config", action="append", default=[])
    ap.add_argument("--nproc", type=int, help="MAPDL processes (default: try each of validate.nproc)")
    a = ap.parse_args(argv)
    cfg = load_config(*a.config)
    stl = a.run_dir / ("opt.stl" if a.raw else "final.stl")
    out = a.run_dir / ("validation_raw.json" if a.raw else "validation.json")

    work = Path(tempfile.mkdtemp(prefix="validate_"))
    try:
        report = validate(stl, geom.load_mesh(stl), cfg, work, [a.nproc] if a.nproc else None)
    except ValueError as e:
        report = {"status": "not_validated", "reason": str(e)}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    report.update(mesh=stl.name)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in ("status", "reason", "converged", "pilot_uz", "compliance",
                                                 "strain_energy", "mass", "specific_stiffness")}, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
