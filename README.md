# Exclude-and-rebuild TO: preserving small features

Plan: [topo-opt-rebuild-plan.md](topo-opt-rebuild-plan.md). Shared parameters live in [config/base.yaml](config/base.yaml), per-part ones in [config/parts/](config/parts/).

## Setup

What you need (Windows):

1. **Ansys 2026 R1** (Mechanical, MAPDL, Prime) with `AWP_ROOT261` set, and access to the licence pool. The pool
   grants one `ansys` seat, so only one Ansys step runs at a time.
2. **Python 3.12** and [uv](https://docs.astral.sh/uv/). Create the environment:

   ```powershell
   uv venv --python 3.12 .venv
   uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
   .venv\Scripts\python.exe -m pytest      # ~3 min; checks the install
   ```

   If Smart App Control blocks uv's venv launcher, use `py -3.12 -m venv .venv` instead and keep the
   `uv pip install` line. Python 3.12 is pinned: PyMechanical's pythonnet and the manifold3d wheels are not
   reliable on 3.14 yet.
3. **Onshape API keys** (Onshape developer portal → API keys), set as *user* environment variables
   `ONSHAPE_ACCESS_KEY` and `ONSHAPE_SECRET_KEY`. Every export uses the company's API allocation.
4. **The Preserve region feature** in your Onshape document: Custom features → Add custom features → this
   project's working document (a released version). [docs/modelling.md](docs/modelling.md) explains how to model
   and mark a part.

**Claude Code:** the project skill `topo-rebuild` ([.claude/skills/topo-rebuild/](.claude/skills/topo-rebuild/))
loads when Claude Code runs in this repo. Ask it to "run <part>" and it runs the chain below, stopping for review
after the geometry check and after the first run.

**Optional, Onshape MCP server:** it is only needed if you want Claude to write or edit FeatureScript (the
Preserve region feature and the test parts were written through it). It is not used by the pipeline. To add it
for yourself:

```powershell
claude mcp add-json --scope user onshape '{"type":"http","url":"https://fs-mcp.labs.onshape.app/mcp","oauth":{"authServerMetadataUrl":"https://mcp-auth.labs.onshape.app/.well-known/oauth-authorization-server"}}'
```

Then log in with `/mcp` in Claude Code. Its calls also use the company's Onshape API allocation.

Validation meshing uses Ansys Prime through `ansys-meshing-prime`, pinned to the client that matches the
installed Ansys release. Point queries (inside tests, distances) use libigl; trimesh's own are too slow without
Embree.

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
