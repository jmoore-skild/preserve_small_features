"""PyMechanical TO run (plan Phase 3): import STEP -> mesh -> static -> level-set TO -> solve -> export.

    python -m pipeline.run_mechanical --run M3_h4            # reads config/runs/M3_h4.yaml
    python -m pipeline.run_mechanical --run M3_h4 --no-solve # build and save the model only

Writes runs/<id>/: config.yaml (resolved), material.xml, model.mechdat, opt.stl, mechanical.json.
API names verified against Ansys 2026 R1 (v261) embedded on 2026-10-15.
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

import yaml

from pipeline.config import REPO, bore_radius, bores, keep_radius, load_config

RUNS_CONFIG = REPO / "config" / "runs"


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

def zone_x_range(cfg: dict, zone: str) -> tuple[float, float]:
    """X range enclosing a zone's bores (bore x ± keep radius), for the worksheet location filter."""
    xs = [b.x for b in bores(cfg) if b.zone == zone]
    R = keep_radius(cfg)
    return min(xs) - R, max(xs) + R


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

    def bore_selection(self, name: str, zone: str | None = None):
        """Worksheet NS: faces with the bore radius, optionally filtered to one zone's X range."""
        A, C, O = self.SelectionActionType, self.SelectionCriterionType, self.SelectionOperatorType
        ns = self.m.AddNamedSelection()
        ns.Name = name
        ns.ScopingMethod = self.GeometryDefineByType.Worksheet

        def row(action, crit, op, value=None, bounds=None):
            c = self.Ansys.ACT.Automation.Mechanical.NamedSelectionCriterion()
            c.Active, c.Action, c.EntityType = True, action, self.SelectionType.GeoFace
            c.Criterion, c.Operator = crit, op
            if value is not None:
                c.Value = self.q(value)
            if bounds is not None:
                c.LowerBound, c.UpperBound = self.q(bounds[0]), self.q(bounds[1])
            ns.GenerationCriteria.Add(c)

        row(A.Add, C.Radius, O.Equal, value=bore_radius(self.cfg))
        if zone is not None:
            row(A.Filter, C.LocationX, O.RangeInclude, bounds=zone_x_range(self.cfg, zone))
        ns.Generate()
        return ns

    def static(self, fix_ns, load_ns):
        lc = self.cfg["loads"]["remote_force"]
        st = self.m.AddStaticStructuralAnalysis()
        fs = st.AddFixedSupport()
        fs.Location = fix_ns
        rf = st.AddRemoteForce()
        rf.Location = load_ns
        rf.DefineBy = self.LoadDefineBy.Components
        comps = [self.q(lc["magnitude"] * d, "N") for d in lc["direction"]]
        rf.XComponent.Output.DiscreteValues = [comps[0]]
        rf.YComponent.Output.DiscreteValues = [comps[1]]
        rf.ZComponent.Output.DiscreteValues = [comps[2]]
        rf.Behavior = getattr(self.LoadBehavior, lc["behavior"].capitalize())
        # Full-design tip deflection, to pin down the optimizer's compliance convention (F*u vs F*u/2).
        uz = st.Solution.AddDirectionalDeformation()
        uz.Location = load_ns
        uz.NormalOrientation = self.NormalOrientationType.ZAxis
        self.static_analysis, self.remote_force, self.static_uz = st, rf, uz
        return {"pilot": [rf.XCoordinate.Value, rf.YCoordinate.Value, rf.ZCoordinate.Value]}

    def topology(self, method: str, bores_ns):
        to = self.cfg["topology_optimization"]
        topo = self.m.AddTopologyOptimizationAnalysis()
        topo.TransferDataFrom(self.static_analysis)
        kids = {type(c).__name__: c for c in topo.Children}
        reg = kids["OptimizationRegion"]
        reg.OptimizationType = self.OptimizationType.TopologyLevelSet
        if method == "M3":
            reg.ExclusionScopingMethod = self.GeometryDefineByType.Component
            reg.ExclusionRegionLocation = bores_ns
            reg.ExclusionThickness = self.q(to["exclusion"]["thickness_manual"])
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

def run(run_id: str, solve: bool = True) -> dict:
    cfg = load_config(RUNS_CONFIG / f"{run_id}.yaml")
    rc = cfg["run"]
    version = cfg["mechanical"]["version"]
    out = REPO / "runs" / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    meta = {"run_id": run_id, "method": rc["method"], "h": rc["h"],
            "started": datetime.now(timezone.utc).isoformat(), "wall_time": {}}

    def timed(key, fn, *a, **kw):
        t = time.perf_counter()
        r = fn(*a, **kw)
        meta["wall_time"][key] = time.perf_counter() - t
        return r

    from ansys.mechanical.core import App
    app = timed("start", App, version=version)
    meta["ansys_version"] = str(app.version)
    meta["ansys_product"] = str(app).splitlines()[0]
    model = Model(app, cfg)

    meta["geometry"] = timed("import", model.import_geometry, REPO / "geometry" / "space.step")
    mat = write_material_xml(cfg, version, out / "material.xml")
    model.assign_material(out / "material.xml", mat)
    ns_all = model.bore_selection("BORES")
    ns_fix = model.bore_selection("BORES_FIX", "FIX")
    ns_load = model.bore_selection("BORES_LOAD", "LOAD")
    meta["named_selections"] = {ns.Name: len(ns.Location.Ids) for ns in (ns_all, ns_fix, ns_load)}
    meta["mesh"] = timed("mesh", model.mesh, rc["h"])
    meta["static"] = model.static(ns_fix, ns_load)
    meta["topology_setup"] = model.topology(rc["method"], ns_all)

    if solve:
        nprocs = cfg["mechanical"]["nproc"]
        meta["cores"] = {"static": timed("solve_static", model.solve, model.static_analysis.Solution, nprocs)}
        uz = model.static_uz
        meta["static_result"] = {"uz_load_bores_avg": uz.Average.Value, "uz_load_bores_min": uz.Minimum.Value,
                                 "uz_load_bores_max": uz.Maximum.Value}
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
    (out / "mechanical.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="run id; reads config/runs/<id>.yaml")
    ap.add_argument("--no-solve", action="store_true", help="build and save the model without solving")
    a = ap.parse_args(argv)
    preload_ansys_openssl(load_config(RUNS_CONFIG / f"{a.run}.yaml")["mechanical"]["version"])
    meta = run(a.run, solve=not a.no_solve)
    print(json.dumps({k: meta[k] for k in ("mesh", "named_selections", "wall_time")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
