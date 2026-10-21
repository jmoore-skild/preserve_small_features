---
name: topo-rebuild
description: Run exclude-and-rebuild topology optimization on an Onshape part marked with Preserve region keeps — export, geometry check, Ansys Mechanical TO, rebuild, measure, validate, report. Use when asked to optimize or "run" a part (e.g. "run the bracket"), to set up a new part, to rerun a step, or to read a part's results.
allowed-tools: Bash(.venv/Scripts/python.exe -m pipeline.*) Bash(.venv/Scripts/python.exe -m scripts.run_matrix *) Bash(tasklist*)
---

# Exclude-and-rebuild topology optimization

The pipeline in this repo optimizes a part in Ansys Mechanical while freezing a wall around each marked feature
(method M3), then rebuilds `final = (opt ∪ keep) ∩ SPACE` so the marked faces come out exactly as modelled.
The user's main goal is **preserving the small features** (holes, seats, pads, slots): report fidelity first,
stiffness second, stresses only as indicative.

Run everything from the repo root with `.venv/Scripts/python.exe` (Python 3.12). Every step takes `--part <part>`
or `--run-dir runs/<part>/<run_id>`.

## 0. Before starting

- **Part config:** `config/parts/<part>.yaml` must exist. For a new part, draft it from
  [part-template.yaml](part-template.yaml) with the user: the Part Studio URL, which regions are fixed, and the
  load (regions, magnitude in N, direction, material). Don't guess loads; ask.
- **Modelling:** the part must follow [docs/modelling.md](../../../docs/modelling.md): one part `SPACE` with
  every small feature cut in, plus keeps from the Preserve region feature (`KEEP_<ROLE>_<label>`), at least
  one FIX and one LOAD region, and **the same wall thickness t on every region** (Mechanical takes one
  Exclusion Thickness per run).
- **Onshape API allocation:** the export (REST, with `ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY`) and any Onshape
  MCP call use the user's company API allocation. Say so before the first Onshape call of a session.
- **Ansys licence:** Mechanical, Prime and MAPDL all need the one `ansys` seat the pool grants. Run Ansys steps
  strictly one at a time, and check that no other solve is running (`tasklist` for `AnsysWBU`, `ansys*`,
  `MAPDL`, `Prime`). The solvers ask for 24, then 12, then 4 cores, so they take every HPC pack the pool grants.

## 1. Export and check — then STOP for review

```
.venv/Scripts/python.exe -m pipeline.onshape_export --part <part>     # 1–10 min (STEP translation); background
.venv/Scripts/python.exe -m pipeline.geometry_check --part <part>     # ~1 min
```

Re-export only if `geometry/<part>/` is missing or the user changed the model. Run the export in the background.

**Stop here.** Show the user the regions (`geometry/<part>/regions.json`: name, role, keep count, face count,
area, t, m) and the check results. If any check fails, explain it with
[troubleshooting.md](troubleshooting.md) and wait for the model to be fixed. Continue only when the user agrees
the regions are right.

## 2. First run: M3 at h = 2 — then STOP for review

```
.venv/Scripts/python.exe -m scripts.run_matrix --part <part> M3_h2    # ~10–20 min; background
```

This runs `run_mechanical` → `rebuild` → `measure` → `validate`, one step per process, and logs each step to
`runs/<part>/M3_h2/<step>.log`. Run it in the background and watch the step lines (`ok` / `FAILED`); don't
poll with sleeps. M3 + rebuild at h = 2, validated on the rebuilt part, is the recommended setting (beam and
bracket studies: within a few % of h = 1 at half the solve time, and exact features).

**Stop here** and report, in this order (see [reading-results.md](reading-results.md)):

1. Face matching: faces per region vs the Preserve region attribute (`mechanical.json` `regions`).
2. Feature fidelity: `metrics.json` `pass.preserved_surface`, max deviation per region.
3. Connectivity: FIX–LOAD connected, PASSIVE regions attached, seam flags.
4. Validated stiffness, mass, specific stiffness, convergence, model-form gap.
5. Indicative stresses, clearly labelled as indicative.

Then ask what to run next. Don't start further runs unprompted.

## 3. Further runs (only when asked)

| Run id | What |
|---|---|
| `M3_h4` | Coarse: about 30–60 % of the h = 2 solve time; 3–10 % less stiff (bracket, beam); may neck at seams |
| `M3_h1` | Reference: about 2× the h = 2 solve time (beam: 385 vs 211 s), about 15 GB MAPDL memory |
| `M0_h2` | Ansys default exclusions, no rebuild (raw), then `M0R_h2` (rebuilt) — for comparison only |
| `M3_h4_X2` | 8 mm exclusion thickness override (shown to hurt on the beam) |

`python -m scripts.run_matrix --part <part> M3_h4 M0_h2` chains several runs; `--skip-solve` reruns the
post-processing on existing TO results. Single steps: `pipeline.run_mechanical --part <part> --run <id>`,
`pipeline.rebuild|measure|validate --run-dir runs/<part>/<id>`. New run settings go in `config/runs/<id>.yaml`.

Summary table and plots for a part: `python -m pipeline.collect --part <part>` → `results/<part>/`.

## Rules

- Never change `config/base.yaml` for one part; put overrides in the part YAML.
- Never run two Ansys steps at once (licence), and never delete `runs/_phase4/` (the beam reference).
- `geometry/` and `runs/` are not committed. Commit config, code and `results/` only when the user asks.
- If something fails, read the step's log, then [troubleshooting.md](troubleshooting.md). Don't retry blindly.
