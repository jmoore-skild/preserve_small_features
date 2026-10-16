# Exclude-and-Rebuild Topology Optimization: Cantilever Evaluation Plan

**Status:** Phases 1–2 complete; Gate 1 passed 2026-10-15. Phase 3 (smoke test) mostly done: both h = 4 runs solved, rebuilt and measured, and M3_h4 validated. Still open: M0R_h4 validation, the BC-equivalence check and a TetGen hang guard, then Gate 2. See Section 13.
**Owner:** Josh. **Executor:** Claude Code, with Josh at the gates.

## 0. How to use this plan

- Read Sections 1–3 before writing any code.
- Work through the phases in order. Phases 1 and 2 can run in parallel.
- Stop at each **Gate** and report to Josh before continuing.
- Keep every parameter in `config/base.yaml`. Never hardcode dimensions, hole positions, or mesh sizes in scripts.
- The decisions in Section 3 are settled. Don't change them without asking.
- API names marked *(verify)* come from memory or from the 2024 R2 documentation. Check them against the installed Ansys version before relying on them.

## 1. Problem and approach

**Problem.** Topology optimization (TO) runs use a coarse, homogeneous mesh for result quality and solve time, and the optimized output is 3D printed directly. In past runs, small interface features such as screw holes came out distorted by the coarse mesh.

**Approach: exclude, then rebuild.**

1. **Exclude.** Mark the interface zones as excluded regions so the optimizer keeps them solid. The FE mesh only needs to know these zones are solid; it doesn't need to resolve their exact shape.
2. **Rebuild.** Restore the exact interface geometry from CAD after optimization:

```
final = (opt ∪ keep) ∩ space
```

| Symbol | Meaning |
|---|---|
| `opt` | Optimized shape exported by Ansys (STL) |
| `keep` | Interface zones as **solid** primitives (no holes), finely tessellated |
| `space` | Full pre-optimization body with the true bores, finely tessellated |

**Why this order.** Near the interfaces, the coarse result is wrong in two directions:

- **Excess material:** chordal facets intrude into the bores, and smoothing bulges the surface past the original skin.
- **Missing material:** smoothing shrinkage erodes bosses and washer seats.

Each operation fixes one direction:

- `∩ space` can only remove material. It deletes the excess and snaps any overshoot back onto the exact CAD surface.
- `∪ keep` can only add material. It restores whatever eroded inside the keep zones.

Intersecting last means bores and outer faces always come from one tessellation (`space`'s). Two things follow from that:

- `keep` can be crude primitives.
- Because the keep meshes are solid, a bore is never booleaned between two different tessellations of the same cylinder.

**Corollary:** intersection never adds material. Any face that must be exact needs a keep zone behind it.

## 2. Questions this study answers

1. **Fidelity.** Are the rebuilt bores and washer seats exact, regardless of mesh size?
2. **Connectivity.** Do FIX and LOAD stay in one body without necking at the seams? PASSIVE attachment is recorded, not pass/fail.
3. **Payoff.** Does a coarse mesh plus rebuild match the fine-mesh reference in validated stiffness per unit mass, at a fraction of the solve time?
4. **Implementation.** Is the native face-offset exclusion (M3) reliable? Is the element-exclusion fallback (M2) viable?

## 3. Settled decisions (don't relitigate)

| Decision | Rationale |
|---|---|
| Level-set TO, not density-based | Exclusion Thickness gives face-scoped exclusions real thickness natively. The level-set is a signed distance function at mesh vertices, so the boundary lies inside elements and the output should be watertight, which the manifold booleans need. The AM overhang constraint is supported. Josh prefers level-set for watertight surfaces. |
| Retain 60% volume | A less aggressive target gives chunkier members, which a coarse mesh resolves better. |
| No minimum member size constraint | It drives up solve time. Level-set needs no density filter, and the mesh sets the smallest feature. As a result, the coarse-vs-fine comparison measures what each mesh produces in practice rather than isolating interface effects. |
| No manufacturing constraints in the core runs | Faster solves and a cleaner comparison. Extrusion isn't available for level-set anyway (per the 2024 R2 docs). |
| Exclusion Thickness set manually to 3.75 mm in marked runs | The Program Controlled default is 2× the average element size, so it would scale with the mesh and confound the h sweep. 3.75 mm equals the keep wall, (Ø12 − Ø4.5)/2, so the frozen zone matches the rebuild's keep cylinder. |
| PASSIVE pattern in the bottom (compression) chord at mid-span | The chord is in the load path, which gives the best chance of attachment. Material near the neutral axis is the first to go, so the neutral axis would be the worst spot. |
| PASSIVE attachment is recorded, not a failure | Ansys documents that unloaded keep regions, and even clamped regions, can end up disconnected. |
| FIX and LOAD in one body is a hard requirement | If they're separated, the rebuild has broken the load path. |
| Detached islands go to `islands.stl` and are excluded from `final.stl` | Keeps the printable output clean, and nothing disappears silently. |
| Split-body marking (M1) dropped | M3 covers the same need natively and avoids the unknowns around multi-body export and contact. |
| No print check | Out of scope. |
| Validation uses TetGen (or gmsh) tet meshes solved with PyMAPDL | Fully scriptable, with BCs matched to the TO model. |

## 4. Parameters (`config/base.yaml`)

**Coordinates.**

- X runs along the beam: root face at x = 0, tip at x = 200.
- Z is vertical: the beam occupies z ∈ [0, 60], and the load acts in −Z.
- Y runs through the thickness: y ∈ [0, 15].
- All bore axes are parallel to Y and go all the way through.

**Zones.**

- **FIX:** root bolt pattern carrying the fixed support.
- **LOAD:** tip bolt pattern carrying the remote force.
- **PASSIVE:** mid-span pattern in the bottom chord, with no boundary conditions.

| Item | Value |
|---|---|
| Envelope | L 200 × H 60 × T 15 mm |
| Bore | Ø4.5 mm through (M4 clearance), r = 2.25 mm |
| FIX bores (x, z) | (10, 20), (30, 20), (10, 40), (30, 40) |
| LOAD bores (x, z) | (190, 20), (190, 40) |
| PASSIVE bores (x, z) | (90, 10), (110, 10) |
| Keep zones | Solid Ø12 mm full-thickness cylinder, coaxial with each bore (R = 6, wall 3.75 mm) |
| Washer footprint (seat metric) | Ø9 mm per bore, on both faces (y = 0 and y = 15) |
| Material (placeholder) | Linear isotropic, E = 2.0 GPa, ν = 0.35, ρ = 1270 kg/m³ (PETG-like). The layout doesn't depend on E; E only scales deflections. |
| Boundary conditions | Fixed support on the FIX bores. Remote force of 200 N in −Z, behavior Deformable, scoped to the LOAD bores. |
| TO settings | Level-set. Objective: minimize compliance. Response constraint: Volume, retain 60%. No member size, no manufacturing constraints. |
| Mesh | Uniform tets, global size h ∈ {4, 2, 1} mm. The reference may relax to 1.5 mm if 1 mm is too slow. Turn curvature/proximity capture off so the bores don't trigger local refinement *(verify property names)*. Use the same method and element order in every run. |
| Onshape STL export | Chord tolerance ≈ 0.01 mm, angle ≈ 2°, units mm |

## 5. Methods and run matrix

| ID | Method | Exclusion setup | Rebuild |
|---|---|---|---|
| M0 | Stand-in for current practice | Defined By: Boundary Condition, All Boundary Conditions. Thickness Program Controlled (2h). PASSIVE unmarked. | No |
| M0R | M0 + rebuild | Post-only, applied to M0's STL | Yes |
| M3 | Primary | Defined By: Named Selection containing all 8 bore faces (worksheet: cylindrical faces with radius 2.25 mm *(verify criterion)*). Exclusion Thickness Manual, 3.75 mm. Extension Isotropic. | Yes |
| M2 | Optional fallback | Element-based Named Selection: elements whose centroid lies inside `keep.stl`. Thickness as small as allowed, since the expansion likely applies here too (checked in the smoke test). | Yes |

**Core matrix (5 solves).**

| h | M0 | M0R | M3 |
|---|---|---|---|
| 4 mm | solve | post-only | solve |
| 2 mm | solve | post-only | solve |
| 1 mm | optional | post-only if M0 ran | solve (**reference**) |

**Optional extras** (one solve each at h = 4; Josh picks which ones at Gate 2):

| ID | Run | Purpose |
|---|---|---|
| X1 | M3 + AM Overhang constraint, build direction +Y (bore axis), 45° | Measure its cost and check how it interacts with the exclusions |
| X2 | M3 with Exclusion Thickness 8 mm (Ansys's 2h default at h = 4); post-processing keep stays Ø12 | Find out whether 3.75 mm holds when it's below one element |
| X3 | M2 | Check that element-based exclusion is viable |
| X4 | Density-based M0 (defaults, Smoothing export) + rebuild | Quantify the watertightness/repair difference vs level-set |
| X5 | M3 + Minimum Member Size 10 mm | Put a number on the member-size cost |
| (no ID) | Smoothing sweep (post-only) | Only if the level-set export path has a smoothing step (decided in Phase 3) |

**Run IDs** take the form `{method}_h{h}[_{extra}]`, for example `M3_h4`, `M0R_h2`, `M3_h4_X2`.

## 6. Repository layout and conventions

```
topo-rebuild-eval/
├── PLAN.md                     # this file
├── config/
│   ├── base.yaml               # all parameters (Section 4)
│   ├── onshape.yaml            # document/workspace/element IDs (no secrets)
│   └── runs/                   # per-run overrides: method, h, extras
├── featurescript/
│   └── TopoTestBeam.fs
├── pipeline/
│   ├── onshape_export.py       # REST export: STEP (SPACE), fine STL (SPACE, KEEP_*)
│   ├── geometry_check.py       # verify exports against base.yaml
│   ├── run_mechanical.py       # PyMechanical: import → mesh → static → level-set TO → export
│   ├── rebuild.py              # (opt ∪ keep) ∩ space + island handling (Section 8)
│   ├── measure.py              # metrics (Section 9)
│   ├── validate.py             # tet mesh → PyMAPDL static (Section 9)
│   ├── synth.py                # synthetic opt meshes with planted defects (Phase 2)
│   └── collect.py              # aggregate → results/summary.csv + plots
├── tests/                      # pytest: rebuild + measure against synth cases
├── geometry/                   # exported STEP/STL, shared by all runs
├── runs/<run_id>/              # config snapshot, .mechdat, opt.stl, final.stl,
│                               # islands.stl, metrics.json, validation.json, logs
└── results/
```

**Conventions.**

- **Units:** millimetres in all files. Check the scale of the Ansys STL on the first export, since Mechanical may write it in SI units.
- **Secrets:** Onshape API keys come from environment variables and are never committed.
- **Run records:** each run saves a snapshot of its resolved config, the Ansys version, and wall times.

**Dependencies:**

- Ansys: `ansys-mechanical-core`, `ansys-mapdl-core`.
- Geometry: `trimesh`, `manifold3d`, `rtree`, `tetgen`, `pyvista`.
- General: `numpy`, `scipy`, `pyyaml`, `pytest`, `matplotlib`.
- Add `gmsh` if validation needs local refinement near the bores.
- For writing CDB files, use whatever PyMAPDL currently recommends *(verify)*. `ansys-mapdl-reader`'s `save_as_archive` is one known route.

## 7. Phases

### Phase 1 — Geometry (FeatureScript + export)

**FeatureScript.**

- [x] Write `featurescript/TopoTestBeam.fs`, a custom feature with these parameters, defaulting to the Section 4 values:
  - envelope dimensions,
  - bore diameter and keep diameter,
  - the three bore patterns as per-zone center + count + pitch: FIX 2×2, LOAD 1×2, PASSIVE 2×1.
- [x] The feature creates these parts, named with `setProperty(... PropertyType.NAME ...)`:
  - `SPACE`: the box minus the 8 through-bores.
  - `KEEP_FIX_1..4`, `KEEP_LOAD_1..2`, `KEEP_PASSIVE_1..2`: solid Ø12 × T cylinders, coaxial with the bores. They overlap `SPACE`, which is fine in a Part Studio.
- [x] Feature written to the working document via the Onshape MCP (Feature Studio `TopoTestBeam`); Josh instanced it in Part Studio 1. IDs are in `config/onshape.yaml`.

Every run uses the same geometry, so no configurations or output-set switches are needed. The exporter selects parts by name.

**Export (`onshape_export.py`).** Look up part IDs by name, then export:

- [x] STEP of `SPACE` only (the FE geometry).
- [x] Fine STL of `SPACE`.
- [x] Fine STL of all `KEEP_*` parts.
- [x] Reuse Josh's existing Onshape REST client if convenient. Check the current endpoint and parameter names *(verify)*: STL `chordTolerance` / `angleTolerance` / `units`, and STEP via the translations endpoint.

**Checks (`geometry_check.py`).** All must pass before anything downstream runs:

- [x] `SPACE` volume = L·H·T − 8·π·r²·T, within ±0.1%.
- [x] Bounding boxes match the envelope.
- [x] Cylinder fits on the `SPACE` bores match the Section 4 coordinates to ≤ 0.01 mm.
- [x] Each keep cylinder is coaxial with its bore.
- [x] All STLs are watertight volumes.
- [x] `keep.stl` is written as the union of the `KEEP_*` parts, as one manifold.

**Gate 1:** Josh reviews the feature in the Onshape GUI (shape, names, any errors) along with the `geometry_check` output.

### Phase 2 — Post-processing dry run (no Ansys; can run in parallel with Phase 1)

The goal is to validate the measuring tools before they measure anything real.

**Code to build.**

- [x] `rebuild.py` and `measure.py`, per Sections 8 and 9.
- [x] `synth.py`: synthetic `opt` meshes built from `space` (or from a procedural stand-in if Phase 1 isn't finished):
  - Start from a beam with chunky lightening holes, at about 60% volume.
  - Voxelize at 4 mm, run marching cubes, then apply Taubin smoothing, to mimic a coarse export.

**Planted defects.** Plant each one on its own and in combination:

- [x] **(a)** Undersized, faceted bores.
- [x] **(b)** An outward bulge past a side face.
- [x] **(c)** A necked seam next to a FIX keep zone.
- [x] **(d)** The PASSIVE region detached as an island.
- [x] **(e)** A non-watertight `opt`.

**Pytest acceptance.** Each metric must flag exactly what was planted:

- [x] (a) and (b) are removed by the rebuild, and the bore and seat metrics pass afterwards.
- [x] (c) shows up as a dip in the seam profile.
- [x] (d) is split off to `islands.stl` and PASSIVE is reported as detached, without failing the run.
- [x] Separating FIX from LOAD is a hard fail.
- [x] (e) fails loudly at the input check with a clear message. No silent repair; any repair policy added later must be logged.

### Phase 3 — Smoke test

**Mechanical run.** Run `run_mechanical.py` end to end for `M3_h4` and `M0_h4`:

- [x] Import the STEP and assign the material.
- [x] Mesh uniformly.
- [x] Create worksheet named selections for the bores.
- [x] Set up the static analysis and BCs.
- [x] Add a linked Structural Optimization (level-set) with design region = `SPACE`, the method's exclusion, the objective, and the response constraint.
- [x] Solve, export the STL, and save the `.mechdat`.
- [x] Write run metadata: element count, iterations, wall time, final volume, and final compliance if it can be retrieved.

**Post-processing.**

- [x] Run `rebuild` + `measure` + `validate` on both runs.
- [ ] If the settings don't change after Gate 2, these two runs count toward the matrix.

**Questions to answer.** Record the answers in `results/smoke_test.md`:

1. How is a level-set result exported to STL from scripting? Is it the same Topology Density + Smoothing route as density-based, or something else?
2. Where does the exported STL land, and in what units and frame? The Smoothing STL path is read-only in density-based. Is the file watertight as expected?
3. Does level-set accept the deformable remote force? The density-based limitations only rule out joint loads, FSI, and EM transducers, and the level-set equivalent hasn't been checked.
4. Does the STEP part name `SPACE` arrive in Mechanical?
5. Does the radius worksheet criterion select exactly the 8 bores?
6. Is the manual Exclusion Thickness honored on a face named selection? Compare the frozen region against the Ø12 cylinders.
7. Does excluded volume count toward the 60% target? The answer decides normalization: compare at matched mass, or by stiffness per mass.
8. What are the defaults for `InitializationHoleSize` / `InitializationModifierType` on `OptimizationRegion`? If they're mesh-relative, pin them in absolute units for all runs.
9. How do the solve time and iteration count at h = 4 project to the h = 1 reference? Decide between 1 mm and 1.5 mm.
10. Can the optimizer's final compliance be retrieved from scripting? It's needed for the model-form gap metric.
11. (Only if X3 is planned.) Does the thickness expansion apply to element-based exclusions, and can it be set near zero?

**Gate 2:** Josh reviews `smoke_test.md`. Adjust the matrix and settings, and pick the extras, before Phase 4.

### Phase 4 — Run matrix and report

- [ ] Run the core matrix (Section 5), then the extras chosen at Gate 2.
- [ ] Process each run: measure M0 runs on their raw STL; for M0R, M3, and M2, run rebuild → measure → validate. Write all artifacts to `runs/<run_id>/`.
- [ ] `collect.py` writes `results/summary.csv` and these plots:
  - bore error vs h, by method,
  - specific stiffness vs TO wall time,
  - volume bookkeeping per run.
- [ ] Write `results/REPORT.md` covering:
  - pass/fail against Section 9,
  - predictions vs outcomes (Section 10),
  - a recommendation for production use.

## 8. Rebuild specification (`rebuild.py`)

**Inputs:** `runs/<id>/opt.stl` (from Ansys), `geometry/keep.stl`, `geometry/space.stl`.

**Steps.**

1. **Input checks.** Any failure is a hard fail:
   - `is_volume` is true for all three meshes.
   - The volume ratio `opt/space` is in (0.3, 1.0). This catches an m-vs-mm error immediately.
   - At least 90% of a subsample of `opt` vertices lie inside `space`. This catches a frame or unit mismatch.
2. **Boolean:** `final = opt.union(keep, engine="manifold").intersection(space, engine="manifold")`, using trimesh with the manifold3d engine.
3. **Components:** split `final` into connected bodies.
   - Probe each keep zone with a point in the middle of its wall: radial distance (r + R)/2 = 4.125 mm from the bore axis, on the +X side, at y = T/2.
   - The main body is the one containing every FIX and LOAD probe. If no such body exists, that's a hard fail.
   - All other bodies go to `islands.stl`, along with which probes each one contains.
   - PASSIVE detachment is recorded, not a failure.
4. **Outputs:** `final.stl` (main body only), `islands.stl`, and `rebuild.json` (volumes, component report).

**Reference sketch:**

```python
import trimesh

opt, keep, space = (trimesh.load(p) for p in (opt_path, keep_path, space_path))
assert all(m.is_volume for m in (opt, keep, space))
assert 0.3 < opt.volume / space.volume < 1.0
assert space.contains(opt.vertices[::20]).mean() > 0.9

final = opt.union(keep, engine="manifold").intersection(space, engine="manifold")
bodies = final.split(only_watertight=False)   # then classify via keep-wall probes
```

## 9. Metrics, pass criteria, and validation

**Metrics.** Computed by `measure.py` and `validate.py`.

| Metric | How | Pass |
|---|---|---|
| Bore fidelity | Per bore: least-squares cylinder fit to `final` vertices in the bore band (radial distance from the nominal axis in [0.5r, r + 1.5 mm], with 0.5 ≤ y ≤ T − 0.5). Report radius error, axis offset, and max radial deviation. Report "missing" if too few vertices. | ≤ 0.02 mm for rebuilt runs (M0R, M3, M2) at every h. M0 is reported only. |
| Seat flatness | Per bore, on both faces: sample a grid over the Ø9 washer annulus and ray-cast along ±Y to the surface. Report the max deviation from the nominal face plane. Rays that miss count as eroded. | ≤ 0.02 mm and no misses, for rebuilt runs. M0 is reported only. |
| Load-path connectivity | Are the FIX and LOAD probes in the same body? | Required |
| PASSIVE attachment | Are the PASSIVE probes in the main body? | Recorded only |
| Seam necks | Material cross-section of `final` through thin cylindrical shells around each keep cylinder, stepping outward in 0.5 mm increments up to the smaller of 2h and half the gap to the nearest other keep zone. A neck shows up as a dip. | Sanity check: flag dips > 25% |
| Volume bookkeeping | V_opt, V_added (∪), V_trimmed (∩), V_final, V_islands | Report |
| Specific stiffness | Validated tip stiffness ÷ mass, for coarse and medium M3 vs the h = 1 M3 reference | Within 10% |
| Model-form gap | Validated compliance vs the optimizer's final compliance | Report |
| Cost | Element count, iterations, TO wall time, speedup vs the reference | Report |

**Validation (`validate.py`).**

- **Mesh:** tet-mesh `final.stl` with TetGen, or gmsh with size fields for bore refinement. Use a global size of about 1.5 mm and about 0.4 mm at the bores, then a second level that halves both. The result counts as converged if tip displacement changes by less than 1% between levels; otherwise refine again.
- **Fixed support:** fix all DOF of the nodes on the FIX bore surfaces (radial distance within 0.05 mm of r, inside the FIX x-range).
- **Load:** put a pilot node at the centroid of the LOAD bore faces, (190, 7.5, 30), and connect it to the LOAD bore-surface nodes with an RBE3. Apply 200 N in −Z at the pilot. This matches Mechanical's deformable remote force *(verify that Mechanical's default pilot location is the centroid of the scoped faces)*.
- **Report:**
  - pilot UZ, compliance, and mass (V × ρ),
  - max von Mises in the members, excluding material within 2 mm of bores and seams,
  - max von Mises at the bores,
  - seam stress sampled at a fixed 1 mm offset from each keep boundary. Sharp creases are singular, so raw peaks there only track mesh size.
- **Scope:** validate every rebuilt `final`, plus raw M0 `opt` meshes where they tet-mesh cleanly.

## 10. Predictions (compare against these in REPORT.md)

- **M0:** FIX/LOAD bosses are reasonably sized (default 2h thickness), but the bores come out faceted at h = 4. PASSIVE is unmarked and likely survives as chord material with mangled bores.
- **M0R:** bores and seats are exact wherever material reaches them. PASSIVE is likely attached, given its chord placement.
- **M3:** bores and seats are exact at every h. FIX/LOAD are well attached, and PASSIVE is very likely attached.
- **Payoff:** at 60% retention the topology is chunky, so coarse M3 should land within 10% of the reference in specific stiffness, with a large saving in solve time.
- **X2:** if the `M3_h4` bosses look under-held (a frozen wall thinner than one element), an 8 mm thickness should fix them.

## 11. Responsibilities and environment

**Claude Code:** all code and tests, any runs it can execute locally, and the reports.

**Josh:**

- Onshape GUI steps (Feature Studio, Part Studio, recording IDs) and the API keys.
- The Ansys license and machine.
- The Gate 1 and Gate 2 reviews.
- Choosing which extras to run.

**Environment:**

- Record the installed Ansys version in every run.
- The documentation cited here is 2024 R2. Re-check the level-set features in the installed version: Exclusion Thickness/Extension and the supported manufacturing constraints.
- Prefer PyMechanical in embedded mode (`App`); fall back to a remote session if needed.

## 12. References

- Level-set TO (requirements, supported constraints, best practices): https://ansyshelp.ansys.com/public/Views/Secured/corp/v242/en/mech_struct_opt/ds_topo_opt_level_set.html
- Optimization Region (Exclusion Thickness/Extension, Local Design Restriction): https://ansyshelp.ansys.com/public/Views/Secured/corp/v242/en/mech_struct_opt/ds_topo_opt_region.html
- Level-set shape processing (signed distance representation): https://ansyshelp.ansys.com/public/Views/Secured/corp/v242/en/mech_struct_opt/ds_topo_shape_process_level_set.html
- Density-based limitations (for X4): https://ansyshelp.ansys.com/public/Views/Secured/corp/v242/en/mech_struct_opt/ds_topo_opt_limitations.html
- Level-set scripting example: https://ansyshelp.ansys.com/public/Views/Secured/corp/v242/en/act_script/act_script_demo_topo_level_set.html
- OptimizationRegion API (2026 R1): https://ansyshelp.ansys.com/public/Views/Secured/corp/v261/en/act_ref/item17322527776022529142199122091963769231821075145203.html
- PyMechanical topology example (density-based; `AddSmoothing` pattern): https://github.com/ansys/pymechanical-embedding-examples/pull/96/files
- Smoothing STL path is read-only (forum thread): https://discuss.ansys.com/discussion/2633/export-stl-file-to-specific-location
- trimesh booleans: https://trimesh.org/trimesh.boolean.html
- Manifold: https://github.com/elalish/manifold

## 13. Progress log and handoff notes

**2026-10-15 — Phases 1–2 done, Gate 1 passed.** Commits `1fd9606`, `30dd950`, `f1fb8c1` on `main`.

Environment:
- Venv: `.venv`, Python 3.12.14 via `uv` (system Python is 3.14; pythonnet/tetgen/manifold3d not reliable there). `uv pip install --python .venv -e ".[dev]"`.
- Ansys installed: 2026 R1 (`v261`, `AWP_ROOT261`) and 2024 R2 (`v242`). PyMechanical 0.13.4 and PyMAPDL 0.74.1 import cleanly but have **not** been connected to Ansys yet.
- Onshape: `skildai.onshape.com`. API keys are user-level env vars `ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY`. The Onshape MCP server is authenticated for FeatureScript work (consumes the enterprise API allocation).
- `geometry/` holds the verified Onshape export (`space.step`, `space.stl`, `keep_parts/`, `keep.stl`; gitignored). Re-create with `python -m pipeline.onshape_export && python -m pipeline.geometry_check`.

Resolved *(verify)* items:
- Onshape STL export: `angleTolerance` is **radians**; `chordTolerance` is in the **output units (mm)**, not metres. A too-fine chord (1e-5) returns HTTP 200 with an empty body. Downloads redirect to a regional host (`cad-usw2.onshape.com`).
- Live FeatureScript library version 3095.

Implementation choices made in Phase 2 (details, not Section 3 decisions):
- Bore fit samples points on the facets in the bore band, not vertices: STL cylinders only have vertices on the two faces, which the y-margin excludes.
- Inside-space input check uses signed distance with a 1 mm tolerance (`rebuild.inside_space_tolerance`) instead of `contains()`, which is ambiguous for vertices lying on `space`'s faces.
- Synthetic coarse `opt` uses manifold's marching tetrahedra on the 4 mm sampled SDF (skimage marching cubes gave non-manifold edges on thin members).
- Seam neck = two-sided valley in shell cross-section area vs radius; flagged when the dip > 25%.
- Bores/seats of a detached PASSIVE zone are reported as `detached` and excluded from pass/fail.
- Sub-1e-4 mm sliver edges are collapsed when leaving manifold, because float32 STL round trips otherwise break watertightness; `rebuild` verifies `final.stl` reloads as a closed volume.

Phase 3 starting points:
- Still to write: `pipeline/run_mechanical.py`, `pipeline/validate.py`, `config/runs/` overrides, `results/smoke_test.md`.
- Use Ansys 2026 R1 (v261) unless there is a reason to prefer v242; record the version in every run.
- `measure.py` needs `--h`; `rebuild.py`/`measure.py` CLIs read `runs/<id>/opt.stl` and `geometry/`.

**2026-10-15 (evening) — Phase 3 mostly done; Gate 2 not yet reached.** Draft results are in `results/smoke_test.md`, which answers Q1–Q10.

Working:
- `pipeline/run_mechanical.py` runs embedded PyMechanical end to end on v261 (run configs in `config/runs/`). Both `M3_h4` and `M0_h4` solved (TO ≈ 40 s each).
- Rebuild and measure pass on `M3_h4` and `M0R_h4`.
- `pipeline/validate.py` validated `M3_h4` and converged (0.31 %). Model-form gap +9.8 %.

Key facts learned (details in `smoke_test.md`):
- **Embedded Mechanical needs Ansys's `libcrypto-3-x64.dll` preloaded** before Python's, or it exits with "legacy Provider loading failed". `run_mechanical.preload_ansys_openssl` handles this.
- **Level-set STL** comes from `TopologyDensity.AddSmoothing()` (Move Limit 0 means no smoothing); it is watertight, in mm, in the CAD frame. The h = 4 level-set closes all the bores.
- **The optimizer's "compliance" is strain energy (½F·u).** Excluded volume counts toward the 60 %. Both runs stopped below 60 % (59.2 % / 57.0 %) after rejected iterations, a Gate 2 question.
- **Exclusion Thickness 3.75 mm is honored only approximately** at h = 4 (89–98 % of each keep annulus filled). The rebuild restores the rest.
- **Smart App Control blocks VTK's `vtkFiltersVerdict` DLL** on Josh's machine, so avoid pyvista cell filters (quality, sizes, probe, `.volume`). Josh chose to keep Smart App Control on.

Validation tooling (deviates from the plan's TetGen-only route):
- The FE surface is `(opt ∪ keep) ∩ remeshed(space)`, using a pymeshlab isotropic remesh of SPACE only. Onshape's flat-face sliver fans exploded TetGen.
- Remeshing `opt` or `final` self-intersects in thin members.
- TetGen 0.8.4 ignores the `maxvolume` keyword (so a raw switch string is used) and segfaults with a bgmesh.
- gmsh's STL reparametrization failed or self-intersected, so it isn't used.
- pymeshlab output varies between calls, so there are retries.
- `ansys.mapdl.reader.save_as_archive` needs explicit `ansys_*` cell arrays, or it numbers the material per cell.

Next session (in order):
1. Guard TetGen against hangs: run `tet_mesh` in a child process with a timeout and retry on a fresh remesh. The full-body check hung 74 min inside TetGen and was stopped; there's no partial result.
2. `python -m scripts.check_full_space`: BC equivalence (validate the full body vs Mechanical's −0.7526 mm).
3. `python -m pipeline.validate --run-dir runs/M0R_h4` (about 25 min).
4. Finish `results/smoke_test.md`, then stop at **Gate 2** for Josh. Recommendations to put to him there:
   - test one tightened convergence setting at h = 4;
   - run `M3_h2` as the timing probe for h = 1 vs 1.5;
   - X2 looks informative.

Run artifacts (`runs/M3_h4`, `runs/M0_h4`, `runs/M0R_h4`) are local only (gitignored). Regenerate them with the commands at the end of `smoke_test.md`.
