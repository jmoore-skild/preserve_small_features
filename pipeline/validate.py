"""Validation solve (plan Section 9): Ansys Prime wraps the STL and meshes quadratic tets, and PyMAPDL
solves a linear static.

    python -m pipeline.validate --run-dir runs/<part>/M3_h4          # validates final.stl
    python -m pipeline.validate --run-dir runs/<part>/M0_h4 --raw    # raw M0 opt.stl (only if it keeps the faces)

BCs match the TO model (the run's mechanical.json): all DOF fixed on the nodes of the supports' regions; each
remote force applied at its Mechanical pilot location and distributed by RBE3 to the nodes of its regions. A
region's nodes are the FE nodes within validate.bc_surface_tol of its S triangles (SPACE's surface inside K).
Quadratic tets (SOLID187). Refines (global and feature size halved) until the compliance ΣF·u changes by less
than validate.convergence_tol; with one load that is its pilot displacement along the load.
Writes validation.json in the run directory.

Meshing goes through Prime's wrapper because the STLs are hard to mesh as given: Onshape's flat faces are
sliver fans, and the rebuild's booleans leave slivers and self-intersections in thin members. TetGen, gmsh,
and Prime meshing the surface as given each crashed, hung, or left near-flat tets on them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

from pipeline import geom, regions as rg
from pipeline.config import geometry_dir, read_run_config

MAX_LEVELS = 3


# --------------------------------------------------------------------------- mesh

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


def _widths(fmt: str) -> list[int]:
    """Field widths of a Fortran format line such as (3i10,6e20.13) or (19i10)."""
    out = []
    for item in fmt.strip().strip("()").split(","):
        m = re.fullmatch(r"(\d*)[ieEfF](\d+)(?:\.\d+)?", item.strip())
        if not m:
            raise ValueError(f"unexpected CDB format {fmt!r}")
        out += [int(m.group(2))] * int(m.group(1) or 1)
    return out


def _fields(line: str, widths: list[int]) -> list[str]:
    out, pos = [], 0
    for w in widths:
        if pos >= len(line):
            break
        out.append(line[pos:pos + w])
        pos += w
    return out


def read_cdb(cdb: Path) -> FeMesh:
    """Nodes and solid elements from a blocked-format CDB (NBLOCK, then EBLOCK,19,SOLID). Parsed here rather than
    with ansys.mapdl.reader, whose import loads VTK rendering DLLs that Smart App Control sometimes blocks."""
    with open(cdb, encoding="ascii", errors="replace") as f:
        lines = f.read().splitlines()
    k = next(i for i, ln in enumerate(lines) if ln.startswith("NBLOCK"))
    nw = _widths(lines[k + 1])
    nnum, xyz = [], []
    k += 2
    while not lines[k].startswith("N,") and lines[k].strip() != "-1":
        f = _fields(lines[k], nw)
        nnum.append(int(f[0]))
        xyz.append([float(v) if v.strip() else 0.0 for v in f[3:6]] + [0.0] * (3 - len(f[3:6])))
        k += 1
    k = next(i for i in range(k, len(lines)) if lines[i].startswith("EBLOCK"))
    if "SOLID" not in lines[k]:
        raise ValueError(f"{cdb.name}: expected a solid EBLOCK, got {lines[k]!r}")
    ew = _widths(lines[k + 1])
    mats, conn = [], []
    k += 2
    while lines[k].strip() != "-1":
        f = [int(v) for v in _fields(lines[k], ew)]
        nodes = f[11:]
        k += 1
        while len(nodes) < f[8]:                       # field 9: node count; tets carry 2 more on the next line
            nodes += [int(v) for v in _fields(lines[k], ew)]
            k += 1
        mats.append(f[0])
        conn.append(nodes[:10] + [nodes[0]] * (10 - len(nodes)))
    nnum_a = np.array(nnum, dtype=np.int64)
    elem = np.array(conn, dtype=np.int64)
    row = np.zeros(nnum_a.max() + 1, dtype=np.int64)
    row[nnum_a] = np.arange(len(nnum_a))
    used = np.zeros(len(nnum_a), dtype=bool)
    used[row[elem.ravel()]] = True
    return FeMesh(cdb, np.array(xyz, dtype=float), nnum_a, row[elem[:, :4]], used, np.unique(mats))


class WrapError(ValueError):
    """Prime's wrap failed or self-intersects at these sizes."""


def prime_mesh(stl: Path, cfg: dict, global_size: float, feature_size: float, cdb: Path
               ) -> tuple[FeMesh, float, list[str]]:
    """prime_mesh_once, retrying a failed wrap with the feature size scaled by each validate.wrap_retry_factors.

    The wrapper occasionally fails on one exact size combination (the bracket at 1.5/0.4 mm: three self-
    intersecting faces, then "Wrapper improve quality failed") and succeeds at sizes 10 % either side.
    Returns the mesh, the feature size used, and the failures."""
    import ansys.meshing.prime as prime

    failures = []
    for factor in [1.0, *cfg["validate"]["wrap_retry_factors"]]:
        f = feature_size * factor
        try:
            return prime_mesh_once(stl, cfg, global_size, f, cdb), f, failures
        except (WrapError, prime.PrimeRuntimeError) as e:
            failures.append(f"{global_size:g}/{f:g} mm: {e}")
            print(f"wrap failed at {global_size:g}/{f:g} mm ({e}); retrying", flush=True)
    raise WrapError(f"Prime could not wrap {stl.name}: {failures}")


def prime_mesh_once(stl: Path, cfg: dict, global_size: float, feature_size: float, cdb: Path) -> FeMesh:
    """Wrap `stl` in Prime (curvature sizing between feature_size and global_size, feature edges recovered),
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
        mesh.wrap(min_size=feature_size, max_size=global_size, growth_rate=vc["growth_rate"],
                  elements_per_gap=vc["wrap_elements_per_gap"], feature_angle=vc["feature_angle"])
        part = model.parts[0]
        found = prime.SurfaceSearch(model).search_zonelets_by_self_intersections(
            part.id, part.get_face_zonelets(), 1, prime.SearchBySelfIntersectionParams(model)).n_found
        if found:
            raise WrapError(f"Prime wrap of {stl.name} at {global_size}/{feature_size} mm self-intersects "
                             f"({found} faces)")
        model.set_global_sizing_params(prime.GlobalSizingParams(model, min=feature_size, max=global_size,
                                                                growth_rate=vc["growth_rate"]))
        mesh.volume_mesh(volume_fill_type=prime.VolumeFillType.TET, quadratic=True)
        mesh.write(str(cdb))
    finally:
        client.exit()
    fe = read_cdb(cdb)
    if not fe.n_elements:
        raise ValueError(f"Prime produced no tets for {stl.name} at {global_size}/{feature_size} mm")
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


# --------------------------------------------------------------------------- boundary conditions

def surface_nodes(fe: FeMesh, tris: trimesh.Trimesh, tol: float) -> np.ndarray:
    """Mask of the used FE nodes within tol of the triangles."""
    lo, hi = tris.bounds
    pts = fe.points
    cand = np.flatnonzero(fe.used & np.all(pts >= lo - tol, axis=1) & np.all(pts <= hi + tol, axis=1))
    mask = np.zeros(len(pts), dtype=bool)
    mask[cand[geom.distance(tris, pts[cand]) <= tol]] = True
    return mask


@dataclass
class BcSurfaces:
    """S triangles per region, and the BCs of the TO run (from its mechanical.json)."""
    s_tris: dict[str, trimesh.Trimesh]
    supports: list[list[str]]      # region names per fixed support
    loads: list[dict]              # {"regions", "force" (N, vector), "pilot" (mm)} per remote force


def bc_surfaces(geo: rg.PartGeometry, mech: dict, cfg: dict) -> BcSurfaces:
    eps = cfg["regions"]["surface_tol"]
    s_tris = {r.name: rg.s_triangles(geo.space, geo.region_keep(r.name), eps) for r in geo.regions}
    return BcSurfaces(s_tris, [e["regions"] for e in mech["supports"]], mech["loads"])


# --------------------------------------------------------------------------- solve

def solve(fe: FeMesh, cfg: dict, bcs: BcSurfaces, mapdl) -> dict:
    """Linear static on a Prime quadratic tet mesh. Returns each pilot's displacement, the compliance ΣF·u, the
    region node masks and nodal von Mises (by row)."""
    vc, mc = cfg["validate"], cfg["material"]
    tol = vc["bc_surface_tol"]
    region_nodes = {n: surface_nodes(fe, t, tol) for n, t in bcs.s_tris.items()}
    fix = np.zeros(len(fe.points), dtype=bool)
    for sel in bcs.supports:
        for n in sel:
            fix |= region_nodes[n]
    loads = []
    for e in bcs.loads:
        m = np.zeros(len(fe.points), dtype=bool)
        for n in e["regions"]:
            m |= region_nodes[n]
        loads.append(m)
    if not fix.any() or not all(m.any() for m in loads):
        raise ValueError(f"no surface nodes for a BC (FIX {fix.sum()}, loads {[int(m.sum()) for m in loads]}): "
                         "the mesh has lost the preserved faces that carry the BCs")
    comps = {"FIXNODES": fe.nnum[fix]}
    comps.update({f"LOADNODES{i}": fe.nnum[m] for i, m in enumerate(loads, start=1)})
    add_node_components(fe.cdb, comps)

    mapdl.clear()
    mapdl.prep7()
    mapdl.cdread("DB", str(fe.cdb))
    mapdl.prep7()                                      # the CDB ends with FINISH
    for mat in fe.materials:
        mapdl.mp("EX", mat, mc["youngs_modulus"])     # MPa
        mapdl.mp("PRXY", mat, mc["poisson_ratio"])
        mapdl.mp("DENS", mat, mc["density"] * 1e-12)  # kg/m^3 -> tonne/mm^3
    et = int(mapdl.get_value("ETYP", 0, "NUM", "MAX")) + 1     # the CDB already defines some types
    rl = int(mapdl.get_value("RCON", 0, "NUM", "MAX")) + 1
    mapdl.et(et, "MASS21")
    mapdl.keyopt(et, 3, 2)                             # 3D mass, no rotary inertia: UX/UY/UZ at the pilot
    mapdl.r(rl, 0)
    pilots = []
    for i, e in enumerate(bcs.loads, start=1):
        pilot = int(fe.nnum.max()) + i
        pilots.append(pilot)
        mapdl.n(pilot, *e["pilot"])
        mapdl.type(et)
        mapdl.real(rl)
        mapdl.e(pilot)
        mapdl.rbe3(pilot, "UXYZ", f"LOADNODES{i}")
    mapdl.cmsel("S", "FIXNODES")
    mapdl.d("ALL", "ALL")
    mapdl.allsel()
    for pilot, e in zip(pilots, bcs.loads):
        for comp, f in zip(("FX", "FY", "FZ"), e["force"]):
            if f:
                mapdl.f(pilot, comp, f)
    mapdl.finish()
    mapdl.slashsolu()
    mapdl.antype("STATIC")
    mapdl.eqslv(vc["solver"], vc["solver_tol"])
    mapdl.solve()
    mapdl.finish()
    mapdl.post1()
    mapdl.set(1, 1)
    disp = [[mapdl.get_value("NODE", p, "U", c) for c in ("X", "Y", "Z")] for p in pilots]
    nnum = np.asarray(mapdl.mesh.nnum)
    seqv = mapdl.post_processing.nodal_eqv_stress()
    row = np.full(max(int(nnum.max()), max(pilots)) + 1, -1, dtype=np.int64)
    row[fe.nnum] = np.arange(len(fe.nnum))
    vm = np.full(len(fe.points), np.nan)
    hit = row[nnum] >= 0
    vm[row[nnum[hit]]] = seqv[hit]
    mapdl.finish()
    along = [float(np.dot(u, e["force"]) / np.linalg.norm(e["force"])) for u, e in zip(disp, bcs.loads)]
    return {"disp": disp, "u_along_load": along,
            "compliance": float(sum(np.dot(u, e["force"]) for u, e in zip(disp, bcs.loads))),
            "vm": vm, "region_nodes": region_nodes, "n_fix": int(fix.sum()), "n_load": [int(m.sum()) for m in loads]}


# --------------------------------------------------------------------------- stresses

def stress_report(fe: FeMesh, vm: np.ndarray, geo: rg.PartGeometry, region_nodes: dict, cfg: dict) -> dict:
    """Indicative von Mises: in the members (away from every keep), on each region's preserved faces, and at a fixed
    offset outside each region's K (seams; the creases themselves are singular)."""
    vc = cfg["validate"]
    pts = fe.points
    used = np.flatnonzero(fe.used)
    outside = -geom.signed_distance(geo.keep, pts[used])           # distance outside the keeps
    members = used[outside > vc["exclude_near_features"]]
    out = {"member_vm_max": float(np.nanmax(vm[members])) if len(members) else None, "regions": {}}

    off, hw = vc["seam_sample_offset"], vc["seam_shell_halfwidth"]
    for r in geo.regions:
        K = geo.region_keep(r.name)
        lo, hi = K.bounds
        near = used[np.all(pts[used] >= lo - off - hw, axis=1) & np.all(pts[used] <= hi + off + hw, axis=1)]
        d = -geom.signed_distance(K, pts[near])
        seam = near[np.abs(d - off) <= hw]
        on = region_nodes[r.name]
        out["regions"][r.name] = {
            "surface_vm_max": float(np.nanmax(vm[on])) if on.any() else None,
            "seam_vm_max": float(np.nanmax(vm[seam])) if len(seam) else None,
            "seam_nodes": int(len(seam)),
        }
    return out


# --------------------------------------------------------------------------- driver

def validate(stl: Path, reference: trimesh.Trimesh, cfg: dict, geo: rg.PartGeometry, bcs: BcSurfaces, work: Path,
             nprocs: list[int] | None = None) -> dict:
    """Mesh `stl` at successively halved sizes and solve until the compliance converges. Mass and volume come
    from `reference` (the stl as loaded), the part that would be printed.

    Prime and MAPDL check out the same base license (`ansys`) and the pool grants one seat, so each level
    meshes with Prime closed before MAPDL starts, and MAPDL exits before the next level meshes."""
    vc = cfg["validate"]
    levels, prev = [], None
    g, f = vc["global_size"], vc["feature_size"]
    for level in range(MAX_LEVELS):
        t0 = time.perf_counter()
        fe, f_used, wrap_failures = prime_mesh(stl, cfg, g, f, work / f"level{level}.cdb")
        t1 = time.perf_counter()
        mapdl, nproc = launch(cfg, work, nprocs)
        try:
            ansys_version = str(mapdl.version)
            res = solve(fe, cfg, bcs, mapdl)
        finally:
            mapdl.exit()
        t2 = time.perf_counter()
        tet_volume = fe.volume()
        rec = {"level": level, "global_size": g, "feature_size": f_used, "wrap_failures": wrap_failures,
               "nodes": int(fe.used.sum()),
               "elements": fe.n_elements, "tet_volume": tet_volume,
               "volume_error": tet_volume / float(reference.volume) - 1,
               "pilot_disp": res["disp"], "pilot_u_along_load": res["u_along_load"], "compliance": res["compliance"],
               "n_fix_nodes": res["n_fix"], "n_load_nodes": res["n_load"], "nproc": nproc,
               "mesh_time": t1 - t0, "solve_time": t2 - t1}
        if prev is not None:
            rec["compliance_change"] = abs(res["compliance"] - prev) / abs(prev)
        levels.append(rec)
        print(f"level {level}: h {g:g}/{f_used:g} mm, {fe.n_elements} tets, compliance {res['compliance']:.6f} N mm, "
              f"pilot u along load {[round(u, 6) for u in res['u_along_load']]} mm"
              + (f", change {rec['compliance_change']:.2%}" if prev is not None else ""), flush=True)
        if prev is not None and rec["compliance_change"] < vc["convergence_tol"]:
            break
        prev = res["compliance"]
        g, f = g / 2, f / 2

    last = levels[-1]
    compliance = last["compliance"]                         # ΣF·u, N*mm
    f2 = sum(float(np.dot(e["force"], e["force"])) for e in bcs.loads)
    volume = float(reference.volume)
    mass = volume * cfg["material"]["density"] * 1e-9      # mm^3 * kg/m^3 -> kg
    return {
        "status": "ok",
        "converged": len(levels) > 1 and last["compliance_change"] < vc["convergence_tol"],
        "levels": levels,
        "pilot_disp": last["pilot_disp"],
        "pilot_u_along_load": last["pilot_u_along_load"],
        "compliance": compliance,
        "strain_energy": 0.5 * compliance,                  # comparable to Mechanical's TO "compliance"
        "stiffness": f2 / compliance,                       # N/mm; |F|/u along the load for a single load
        "volume": volume,
        "mass": mass,
        "specific_stiffness": f2 / compliance / mass,       # N/mm/kg
        "stress": stress_report(fe, res["vm"], geo, res["region_nodes"], cfg),
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
    ap.add_argument("--stl", type=Path, help="validate this mesh instead (e.g. geometry/<part>/space.stl for the "
                                             "full-design BC check); writes validation_<stem>.json")
    ap.add_argument("--nproc", type=int, help="MAPDL processes (default: try each of validate.nproc)")
    a = ap.parse_args(argv)
    cfg = read_run_config(a.run_dir)
    geo = rg.load_part_geometry(geometry_dir(cfg["part"]))
    mech = json.loads((a.run_dir / "mechanical.json").read_text(encoding="utf-8"))
    if a.stl:
        stl, out = a.stl, a.run_dir / f"validation_{a.stl.stem}.json"
    else:
        stl = a.run_dir / ("opt.stl" if a.raw else "final.stl")
        out = a.run_dir / ("validation_raw.json" if a.raw else "validation.json")

    work = Path(tempfile.mkdtemp(prefix="validate_"))
    try:
        report = validate(stl, geom.load_mesh(stl), cfg, geo, bc_surfaces(geo, mech, cfg), work,
                          [a.nproc] if a.nproc else None)
    except ValueError as e:
        report = {"status": "not_validated", "reason": str(e)}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    report.update(mesh=str(stl))
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in ("status", "reason", "converged", "pilot_u_along_load", "compliance",
                                                 "strain_energy", "mass", "specific_stiffness")}, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
