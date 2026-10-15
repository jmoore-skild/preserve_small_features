# Exclude-and-rebuild TO: cantilever evaluation

Plan: [topo-opt-rebuild-plan.md](topo-opt-rebuild-plan.md). All parameters live in [config/base.yaml](config/base.yaml).

## Setup

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[dev]"
.venv\Scripts\activate
```

Python 3.12 is pinned: PyMechanical's pythonnet and the tetgen/manifold3d wheels are not reliable on 3.14 yet.

## Commands

| Step | Command |
|---|---|
| Export from Onshape (needs `ONSHAPE_ACCESS_KEY`, `ONSHAPE_SECRET_KEY`, IDs in `config/onshape.yaml`) | `python -m pipeline.onshape_export` |
| Check exports, write `geometry/keep.stl` | `python -m pipeline.geometry_check` |
| Procedural stand-in geometry for dry runs (no Onshape) | `python -m pipeline.geometry_check --write-standin` |
| Synthetic `opt` meshes with planted defects | `python -m pipeline.synth --out runs/synth` |
| Rebuild `(opt ∪ keep) ∩ space` | `python -m pipeline.rebuild --run-dir runs/<id>` |
| Metrics (rebuilt runs) | `python -m pipeline.measure --run-dir runs/<id> --h 4` |
| Metrics (raw M0, report-only) | `python -m pipeline.measure --run-dir runs/<id> --h 4 --raw` |
| Tests | `pytest` |

`geometry/export.json` records whether the geometry came from Onshape or the stand-in.
