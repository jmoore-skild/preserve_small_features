"""PyMechanical TO run (plan Phase 3): import STEP -> mesh -> static -> level-set TO -> solve -> export.

    python -m pipeline.run_mechanical --part bracket --run M3_h4            # config/parts + config/runs
    python -m pipeline.run_mechanical --part bracket --run M3_h4 --no-solve # build and save the model only

Reads geometry/<part>/space.step and the part's regions (geometry_check must have passed). After meshing, each
CAD face of SPACE is matched to a preserve region from its mesh nodes (regions.match_faces), giving one named
selection NS_<ROLE>_<label> per region. Supports and remote forces come from the part YAML; M3 excludes every
region's faces with Exclusion Thickness = the regions' wall thickness t.

Writes runs/<part>/<id>/: config.yaml (resolved), material.xml, model.mechdat, opt.stl, mechanical.json.
API names verified against Ansys 2026 R1 (v261) embedded on 2026-10-15; GeoData faces, MeshData nodes and the
exclusion API on 2026-10-20.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import yaml

from pipeline.config import geometry_dir, load_run_config, run_dir

if TYPE_CHECKING:     # imported in run(): trimesh loads Python's libcrypto (see preload_ansys_openssl)
    from pipeline import regions as rg


# --------------------------------------------------------------------------- Ansys environment

def awp_root(version: int) -> Path:
    root = os.environ.get(f"AWP_ROOT{version}")
    if not root:
        raise RuntimeError(f"AWP_ROOT{version} is not set: Ansys {version} is not installed")
    return Path(root)


def preload_ansys_openssl(version: int) -> None:
    """Load Ansys's libcrypto before Python's.

    Embedded Mechanical otherwise binds to the Python distribution's libcrypto-3-x64.dll (same file
    name, loaded first by hashlib/ssl) and exits with "legacy Provider loading failed". Must run
    before anything imports hashlib or ssl, so it is called at the top of main().
    """
    dll = awp_root(version) / "aisol" / "bin" / "winx64" / "libcrypto-3-x64.dll"
    if dll.exists():
        ctypes.WinDLL(str(dll))


# --------------------------------------------------------------------------- material

def write_material_xml(cfg: dict, version: int, out: Path) -> str:
    """Clone the shipped template material, overwrite E, nu, rho (SI in MatML), rename, write MatML."""
    mc = cfg["material"]
    src = awp_root(version) / "Addins" / "EngineeringData" / "Samples" / "General_Materials.xml"
    tree = ET.parse(src)
    root = tree.getroot()
    doc = root.find("Materials/MatML_Doc")
    param_id = {d.findtext("Name"): d.get("id") for d in doc.iter("ParameterDetails")}

    template = None
    for m in doc.findall("Material"):
        if m.findtext("BulkDetails/Name") == mc["template"]:
            template = m
        else:
            doc.remove(m)
    if template is None:
        raise RuntimeError(f"template material {mc['template']!r} not found in {src}")

    E = mc["youngs_modulus"] * 1e6          # MPa -> Pa
    nu = mc["poisson_ratio"]
    values = {
        "Young's Modulus": E,
        "Poisson's Ratio": nu,
        "Bulk Modulus": E / (3 * (1 - 2 * nu)),
        "Shear Modulus": E / (2 * (1 + nu)),
        "Density": mc["density"],           # kg/m^3
    }
    bulk = template.find("BulkDetails")
    bulk.find("Name").text = mc["name"]
    found = set()
    for pv in bulk.iter("ParameterValue"):
        for name, v in values.items():
            if pv.get("parameter") == param_id.get(name):
                pv.find("Data").text = repr(float(v))
                found.add(name)
    missing = {"Young's Modulus", "Poisson's Ratio", "Density"} - found
    if missing:
        raise RuntimeError(f"template material lacks {sorted(missing)}")
    tree.write(out, encoding="UTF-8", xml_declaration=True)
    return mc["name"]


# --------------------------------------------------------------------------- model setup

def exclusion_thickness(cfg: dict, regions: list[rg.Region]) -> float:
    """M3's Exclusion Thickness: the run's thickness_override, else the regions' common wall thickness t.

    Mechanical has one Exclusion Thickness per optimization region. Its extra exclusion regions (AddExclusionRegion,
    now LocalDesignRestriction) have no thickness (checked on v261, 2026-10-20), so regions must share one t."""
    over = cfg["topology_optimization"]["exclusion"].get("thickness_override")
    if over is not None:
        return float(over)
    ts = sorted({r.wall_thickness for r in regions})
    if len(ts) != 1:
        raise ValueError(f"preserve regions have different wall thicknesses {ts} mm; Mechanical takes one Exclusion "
                         "Thickness per optimization, so give every Preserve region of the part the same t")
    return ts[0]


class Model:
    """Builds the Mechanical model inside an embedded App. Every Mechanical global lives in self.g."""

    def __init__(self, app, cfg: dict):
        self.app = app
        self.cfg = cfg
        g = {}
        app.update_globals(g)
        self.g = g
        self.m = g["Model"]

    def __getattr__(self, name):                 # Mechanical globals: Quantity, enums, ExtAPI, ...
        try:
            return self.__dict__["g"][name]
        except KeyError:
            raise AttributeError(name) from None

    def q(self, value: float, unit: str = "mm"):
        return self.Quantity(value, unit)

    def solve(self, solution, nprocs: list[int]) -> int:
        """Solve on the first core count in `nprocs` that finishes; returns it. Above 4 cores each step needs
        another HPC pack from the shared pool, and a denied checkout stops the solver at startup."""
        settings = self.ExtAPI.Application.SolveConfigurations["My Computer"].SolveProcessSettings
        for n in nprocs:
            settings.MaxNumberOfCores = n
            solution.Solve(True)
            if solution.Status == self.SolutionStatusType.Done:
                return n
            print(f"Mechanical solve on {n} cores did not finish ({solution.Status}), trying fewer", flush=True)
            solution.ClearGeneratedData()
        raise RuntimeError(f"Mechanical solve failed at every core count {nprocs}")

    def import_geometry(self, step: Path):
        self.ExtAPI.Application.ActiveUnitSystem = self.MechanicalUnitSystem.StandardNMM
        gi = self.m.GeometryImportGroup.AddGeometryImport()
        prefs = self.Ansys.ACT.Mechanical.Utilities.GeometryImportPreferences()
        prefs.ProcessNamedSelections = True
        gi.Import(str(step), self.Ansys.Mechanical.DataModel.Enums.GeometryImportPreference.Format.Automatic, prefs)
        bodies = self.m.Geometry.GetChildren(self.DataModelObjectCategory.Body, True)
        if len(bodies) != 1:
            raise RuntimeError(f"expected one body in {step}, got {[b.Name for b in bodies]}")
        self.body = bodies[0]
        return {"body_name": self.body.Name, "body_volume": self.body.Volume.Value}

    def assign_material(self, xml: Path, name: str):
        self.m.Materials.Import(str(xml))
        self.body.Material = name

    def mesh(self, h: float):
        mc = self.cfg["mesh"]
        mesh = self.m.Mesh
        mesh.ElementSize = self.q(h)
        mesh.UseAdaptiveSizing = False
        mesh.CaptureCurvature = mc["capture_curvature"]
        mesh.CaptureProximity = mc["capture_proximity"]
        method = mesh.AddAutomaticMethod()
        sel = self.ExtAPI.SelectionManager.CreateSelectionInfo(self.SelectionTypeEnum.GeometryEntities)
        sel.Ids = [self.body.GetGeoBody().Id]
        method.Location = sel
        method.Method = self.MethodType.AllTriAllTet
        method.Algorithm = self.MeshMethodAlgorithm.PatchConforming
        mesh.GenerateMesh()
        return {"nodes": mesh.Nodes, "elements": mesh.Elements}

    def face_nodes(self) -> tuple[dict[int, np.ndarray], dict[int, float]]:
        """Mesh node coordinates (mm) and area (mm²) of every CAD face of the body. Needs the mesh."""
        md = self.ExtAPI.DataModel.MeshDataByName("Global")
        if str(md.Unit) != "mm":
            raise RuntimeError(f"mesh data unit is {md.Unit}, expected mm")
        pts, area = {}, {}
        for f in self.body.GetGeoBody().Faces:
            pts[f.Id] = np.array([[n.X, n.Y, n.Z] for n in md.MeshRegionById(f.Id).Nodes], dtype=float)
            area[f.Id] = float(f.Area)
        return pts, area

    def face_selection(self, name: str, face_ids: list[int]):
        ns = self.m.AddNamedSelection()
        ns.Name = name
        sel = self.ExtAPI.SelectionManager.CreateSelectionInfo(self.SelectionTypeEnum.GeometryEntities)
        sel.Ids = list(face_ids)
        ns.Location = sel
        return ns

    def static(self, supports: list[dict], loads: list[dict]) -> list[dict]:
        """supports / loads: the part YAML entries, each with "ns" (its named selection) added. One remote force
        per load entry, components = magnitude × unit direction. Returns each load's regions, force and pilot."""
        st = self.m.AddStaticStructuralAnalysis()
        for e in supports:
            if e.get("type", "fixed") != "fixed":
                raise ValueError(f"support type {e['type']!r} not implemented (fixed only)")
            fs = st.AddFixedSupport()
            fs.Location = e["ns"]
        out, self.load_deformations = [], []
        for e in loads:
            if e.get("type", "remote_force") != "remote_force":
                raise ValueError(f"load type {e['type']!r} not implemented (remote_force only)")
            d = np.asarray(e["direction"], dtype=float)
            force = e["magnitude"] * d / np.linalg.norm(d)
            rf = st.AddRemoteForce()
            rf.Location = e["ns"]
            rf.DefineBy = self.LoadDefineBy.Components
            rf.XComponent.Output.DiscreteValues = [self.q(force[0], "N")]
            rf.YComponent.Output.DiscreteValues = [self.q(force[1], "N")]
            rf.ZComponent.Output.DiscreteValues = [self.q(force[2], "N")]
            rf.Behavior = getattr(self.LoadBehavior, e["behavior"].capitalize())
            # Full-design deflection of the loaded faces, to compare with the validation chain.
            comps = []
            for axis in ("XAxis", "YAxis", "ZAxis"):
                u = st.Solution.AddDirectionalDeformation()
                u.Location = e["ns"]
                u.NormalOrientation = getattr(self.NormalOrientationType, axis)
                comps.append(u)
            self.load_deformations.append(comps)
            out.append({"regions": e["regions"], "force": force.tolist(), "behavior": e["behavior"],
                        "pilot": [rf.XCoordinate.Value, rf.YCoordinate.Value, rf.ZCoordinate.Value]})
        self.static_analysis = st
        return out

    def static_result(self, loads: list[dict]) -> list[dict]:
        """Average deflection of each load's faces, and its component along the load."""
        out = []
        for e, comps in zip(loads, self.load_deformations):
            u = np.array([c.Average.Value for c in comps])
            d = np.asarray(e["force"]) / np.linalg.norm(e["force"])
            out.append({"regions": e["regions"], "u_avg": u.tolist(), "u_along_load_avg": float(u @ d)})
        return out

    def topology(self, method: str, exclusion_ns=None, thickness: float | None = None):
        to = self.cfg["topology_optimization"]
        topo = self.m.AddTopologyOptimizationAnalysis()
        topo.TransferDataFrom(self.static_analysis)
        kids = {type(c).__name__: c for c in topo.Children}
        reg = kids["OptimizationRegion"]
        reg.OptimizationType = self.OptimizationType.TopologyLevelSet
        if method == "M3":
            reg.ExclusionScopingMethod = self.GeometryDefineByType.Component
            reg.ExclusionRegionLocation = exclusion_ns
            reg.ExclusionThickness = self.q(thickness)
            reg.ExclusionExtension = getattr(self.ExclusionType, to["exclusion"]["extension"].capitalize())
        elif method == "M0":
            reg.ExclusionScopingMethod = self.GeometryDefineByType.BoundaryCondition
            reg.BoundaryCondition = self.BoundaryConditionType.AllLoadsAndSupports
        else:
            raise ValueError(f"method {method!r} not implemented")

        kids["MassConstraint"].Delete()
        vc = topo.AddVolumeConstraint()
        vc.PercentageToRetain = round(100 * to["retain_volume_fraction"])   # API takes an integer percent
        self.topo, self.region, self.volume_constraint = topo, reg, vc
        return {"region_api": region_api(reg), "region": props(reg), "constraint": props(vc), "objective": props(kids["Objective"]),
                "analysis_settings": props(kids["TopoOptAnalysisSettings"])}


def region_api(reg) -> dict:
    """OptimizationRegion values that may not be shown in the details view (plan Q8)."""
    out = {}
    for name in ("ExclusionThickness", "InitializationHoleSize", "InitializationModifierType",
                 "ExclusionExtension", "OptimizationType"):
        try:
            out[name] = str(getattr(reg, name))
        except Exception as e:                  # not applicable to this optimization type
            out[name] = f"unavailable: {e}"
    return out


def read_objective_history(path: Path) -> dict:
    """Parse ObjectiveHistory.out: iterations, objective (compliance) and volume per iteration."""
    text = path.read_text(encoding="utf-8", errors="replace")
    cols = [c.split(">")[1].split("<")[0] for c in text.split("<COLUMN")[1:]]
    body = text.split("<COLDATA>")[1].split("</COLDATA>")[0]
    rows = [[float(v) if v != "---" else None for v in line.split()] for line in body.strip().splitlines()]
    hist = {c: [r[i] for r in rows] for i, c in enumerate(cols)}
    vol = next(c for c in cols if c.startswith("Volume_Constraint") and c.endswith("_Upper"))
    return {"columns": cols, "iterations": int(hist["Iteration"][-1]),
            "compliance_initial": hist["Objective"][0], "compliance_final": hist["Objective"][-1],
            "volume_initial": hist[vol][0], "volume_final": hist[vol][-1], "history": hist}


def export_shape(model: "Model", cfg: dict, dest: Path) -> dict:
    """Level-set result -> STL via a Smoothing child of Topology Density (plan Q1).

    Mechanical writes the file to a fixed path in the project's _Mech_Files folder; copy it out.
    Move Limit 0 (the level-set default) means no smoothing: the facets are the level-set surface.
    """
    sol = model.topo.Solution
    td = next(c for c in sol.Children if type(c).__name__ == "TopologyDensity")
    sm = td.AddSmoothing()
    sm.MoveLimit = cfg["topology_optimization"]["export_smoothing_move_limit"]
    sol.EvaluateAllResults()
    shutil.copyfile(sm.FileName, dest)
    return {"topology_density": props(td), "smoothing": props(sm), "source_stl": sm.FileName}


def props(obj) -> dict:
    out = {}
    for p in obj.VisibleProperties:
        try:
            out[p.Caption] = p.StringValue
        except Exception:                       # some properties have no display string
            pass
    return out


# --------------------------------------------------------------------------- run

class FaceMatchError(RuntimeError):
    """CAD faces don't match the preserve regions (count or area differs from the Preserve region attribute)."""


def run(part: str, run_id: str, solve: bool = True) -> dict:
    from pipeline import regions as rg       # after preload_ansys_openssl: trimesh imports hashlib
    cfg = load_run_config(part, run_id)
    rc = cfg["run"]
    version = cfg["mechanical"]["version"]
    geo = rg.load_part_geometry(geometry_dir(part))
    out = run_dir(part, run_id)
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    meta = {"part": part, "run_id": run_id, "method": rc["method"], "h": rc["h"],
            "started": datetime.now(timezone.utc).isoformat(), "wall_time": {}}

    def timed(key, fn, *a, **kw):
        t = time.perf_counter()
        r = fn(*a, **kw)
        meta["wall_time"][key] = time.perf_counter() - t
        return r

    def save_meta():
        (out / "mechanical.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")

    from ansys.mechanical.core import App
    app = timed("start", App, version=version)
    meta["ansys_version"] = str(app.version)
    meta["ansys_product"] = str(app).splitlines()[0]
    model = Model(app, cfg)

    meta["geometry"] = timed("import", model.import_geometry, geometry_dir(part) / "space.step")
    mat = write_material_xml(cfg, version, out / "material.xml")
    model.assign_material(out / "material.xml", mat)
    meta["mesh"] = timed("mesh", model.mesh, rc["h"])

    # Preserve regions -> CAD faces -> one named selection per region.
    pts, area = model.face_nodes()
    matches = timed("face_matching", rg.match_faces, pts, geo, cfg["regions"]["surface_tol"])
    checks = rg.check_matches(matches, area, geo, cfg["regions"]["match_area_tol"])
    meta["regions"] = checks
    meta["unmatched_faces"] = len(pts) - sum(len(v) for v in matches.values())
    if not all(c["ok"] for c in checks):
        save_meta()
        bad = [{k: c[k] for k in ("region", "face_count", "area_mm2", "attribute_face_count", "attribute_area_mm2")}
               for c in checks if not c["ok"]]
        raise FaceMatchError(f"CAD faces don't match the preserve regions: {bad}")
    ns = {r.name: model.face_selection(f"NS_{r.name}", matches[r.name]) for r in geo.regions}

    def bc_entries(kind: str, role: str) -> list[dict]:
        """The part YAML's supports or loads, each with its regions resolved and a named selection."""
        names, problems = rg.resolve_bc_regions(cfg[kind], role, geo.regions)
        if problems:
            raise ValueError(f"part YAML {kind}: {problems}")
        out_ = []
        for i, (e, sel) in enumerate(zip(cfg[kind], names), start=1):
            loc = ns[sel[0]] if len(sel) == 1 else model.face_selection(
                f"NS_{kind.upper()}_{i}", [f for n in sel for f in matches[n]])
            out_.append({**e, "regions": sel, "ns": loc})
        return out_

    supports = bc_entries("supports", "FIX")
    meta["supports"] = [{k: v for k, v in e.items() if k != "ns"} for e in supports]
    meta["loads"] = model.static(supports, bc_entries("loads", "LOAD"))
    if rc["method"] == "M3":
        t = exclusion_thickness(cfg, geo.regions)
        excl = model.face_selection("NS_EXCLUSION", [f for r in geo.regions for f in matches[r.name]])
        meta["exclusion"] = {"thickness": t, "faces": len(excl.Location.Ids)}
        meta["topology_setup"] = model.topology("M3", excl, t)
    else:
        meta["topology_setup"] = model.topology(rc["method"])

    if solve:
        nprocs = cfg["mechanical"]["nproc"]
        meta["cores"] = {"static": timed("solve_static", model.solve, model.static_analysis.Solution, nprocs)}
        meta["static_result"] = model.static_result(meta["loads"])
        meta["cores"]["topology"] = timed("solve_topology", model.solve, model.topo.Solution, nprocs)
        meta["solution"] = props(model.topo.Solution)
        solver_dir = Path(model.topo.WorkingDir)
        shutil.copytree(solver_dir, out / "solver_topo", dirs_exist_ok=True)
        hist = read_objective_history(solver_dir / "ObjectiveHistory.out")
        meta["optimizer"] = {k: v for k, v in hist.items() if k != "history"}
        (out / "objective_history.json").write_text(json.dumps(hist["history"], indent=1), encoding="utf-8")
        meta["export"] = timed("export", export_shape, model, cfg, out / "opt.stl")

    app.save(str(out / "model.mechdat"))
    meta["finished"] = datetime.now(timezone.utc).isoformat()
    save_meta()
    return meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", required=True, help="part name; reads config/parts/<part>.yaml and geometry/<part>/")
    ap.add_argument("--run", required=True, help="run id; reads config/runs/<id>.yaml")
    ap.add_argument("--no-solve", action="store_true", help="build and save the model without solving")
    a = ap.parse_args(argv)
    preload_ansys_openssl(load_run_config(a.part, a.run)["mechanical"]["version"])
    meta = run(a.part, a.run, solve=not a.no_solve)
    print(json.dumps({k: meta.get(k) for k in ("mesh", "regions", "unmatched_faces", "loads", "exclusion",
                                               "wall_time")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
