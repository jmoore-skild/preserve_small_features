# Exclude-and-rebuild TO: preserving small features

Plan: [topo-opt-rebuild-plan.md](topo-opt-rebuild-plan.md). Shared parameters live in [config/base.yaml](config/base.yaml), per-part ones in [config/parts/](config/parts/).

## Setup

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[dev]"
.venv\Scripts\activate
```

Python 3.12 is pinned: PyMechanical's pythonnet and the manifold3d wheels are not reliable on 3.14 yet. Validation meshing uses Ansys Prime through `ansys-meshing-prime`, pinned to the client that matches the installed Ansys release. Point queries (inside tests, distances) use libigl; trimesh's own are too slow without Embree.

Smart App Control blocks uv's venv launcher on Josh's machine: if `uv venv` fails, use `python -m venv .venv` and `uv pip install --python .venv/Scripts/python.exe -e ".[dev]"`.

## Commands

Every part has a config `config/parts/<part>.yaml` (Onshape Part Studio, supports and loads by region, overrides of
`config/base.yaml`); its geometry goes to `geometry/<part>/` and its runs to `runs/<part>/<run_id>/`. Run ids
(`M3_h2`, `M0_h4`, ...) are shared settings in `config/runs/`. Model a part as described in
[docs/modelling.md](docs/modelling.md).

| Step | Command |
|---|---|
| Export SPACE and the keeps from Onshape (needs `ONSHAPE_ACCESS_KEY`, `ONSHAPE_SECRET_KEY`) | `python -m pipeline.onshape_export --part <part>` |
| Check the export, write `geometry/<part>/keep.stl` | `python -m pipeline.geometry_check --part <part>` |
| TO run in Mechanical (embedded, Ansys 2026 R1) | `python -m pipeline.run_mechanical --part <part> --run M3_h2` |
| Rebuild `(opt ∪ keep) ∩ space` | `python -m pipeline.rebuild --run-dir runs/<part>/<id>` |
| Metrics (rebuilt runs) | `python -m pipeline.measure --run-dir runs/<part>/<id>` |
| Metrics (raw M0, report-only) | `python -m pipeline.measure --run-dir runs/<part>/<id> --raw` |
| Validation: Prime mesh + PyMAPDL solve (~10 min per run) | `python -m pipeline.validate --run-dir runs/<part>/<id>` |
| Full-design BC check: validate SPACE itself with a run's BCs | `python -m pipeline.validate --run-dir runs/<part>/<id> --stl geometry/<part>/space.stl` |
| Whole chain per run (solve → rebuild → measure → validate; M0 → M0R) | `python -m scripts.run_matrix --part <part> M3_h2 M0_h2` |
| Summary table and plots, `results/<part>/` | `python -m pipeline.collect --part <part>` |
| Synthetic `opt` meshes with planted defects (procedural beam) | `python -m pipeline.synth --out runs/synth` |
| Tests | `pytest` |

M0R runs are post-only: `run_matrix` copies `opt.stl`, `config.yaml` and `mechanical.json` from `M0_h<h>` into
`M0R_h<h>`, then rebuilds, measures and validates.

Rebuild, measure and validate read the part and h from the run's saved `config.yaml`, and validate takes the
BCs (regions, force vectors, pilot locations) from its `mechanical.json`.

The Phase 1–4 cantilever runs are archived in `runs/_phase4/` and its old exports in `geometry/_phase1/`; the beam
is now part `beam` (hand-modelled keeps) and `beam_marked` (Preserve region keeps).
