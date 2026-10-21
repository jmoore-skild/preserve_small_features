# Exclude-and-Rebuild Topology Optimization: Cantilever Evaluation Plan

**Status:** Phases 1–3 complete; Gate 1 passed 2026-10-15. Phase 3 (smoke test) finished 2026-10-16: all h = 4 runs solved, rebuilt, measured and validated, and the BC-equivalence check passes. Gate 2 passed 2026-10-16. Phase 4 done 2026-10-16: `results/REPORT.md` is written. Phases 5–8, which extend the method to general geometry that Josh models in Onshape, were planned 2026-10-20. Phase 5 done and **Gate 3 passed** (2026-10-20). **Next: Phase 6.** See Section 13.
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

**General geometry (Phases 5–8), agreed 2026-10-20:**

| Decision | Rationale |
|---|---|
| Josh models each part in Onshape: one part named `SPACE` plus `KEEP_<ROLE>_<label>` parts, with roles FIX, LOAD and PASSIVE | The exporter already selects parts by name, and the role in the name makes the model readable in the GUI. |
| Keep solids come from a custom "Preserve region" feature (faces + wall thickness + role). Hand-modelled `KEEP_*` parts are also allowed. | Selecting the faces that must stay exact is the natural CAD step, and it works for any shape, not just Y-axis bores. |
| Exclusion and BC faces are found geometrically (the `SPACE` faces inside a keep), not named | Onshape's STEP export carries no face names, and nothing extra has to be named. |
| Loads, supports, material and TO overrides live in a YAML file per part | Easy to change, and versioned alongside the runs. |
| Packaging: a Claude Code skill, shipped as a plugin for teammates. No new MCP server. | The pipeline is a local CLI that needs Ansys licences; what's missing is the workflow knowledge. The existing Onshape MCP covers the FeatureScript work. |

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
preserve_small_features/
├── topo-opt-rebuild-plan.md    # this file
├── config/
│   ├── base.yaml               # shared parameters (Section 4)
│   ├── onshape.yaml            # document/workspace IDs, STL export settings (no secrets)
│   ├── parts/<part>.yaml       # Part Studio, supports and loads by region, overrides of base.yaml
│   └── runs/<run_id>.yaml      # per-run overrides: method, h, extras (shared by all parts)
├── featurescript/              # PreserveRegion.fs, TopoTestBeam.fs, TestBracket.fs
├── docs/modelling.md           # how to model and mark a part
├── pipeline/
│   ├── onshape_export.py       # REST export: STEP (SPACE), fine STL (SPACE, KEEP_*), regions.json
│   ├── geometry_check.py       # generic checks on the export, writes keep.stl
│   ├── regions.py              # regions, S and P, face matching, PartGeometry
│   ├── run_mechanical.py       # PyMechanical: import → mesh → face matching → static → level-set TO → export
│   ├── rebuild.py              # (opt ∪ keep) ∩ space + island handling (Section 8)
│   ├── measure.py              # metrics (Phase 6 table, Section 9)
│   ├── validate.py             # Prime tet mesh → PyMAPDL static (Section 9)
│   ├── synth.py                # synthetic opt meshes with planted defects (Phase 2)
│   └── collect.py              # aggregate → results/<part>/summary.csv + plots
├── scripts/run_matrix.py       # solve → rebuild → measure → validate per run
├── tests/                      # pytest: rebuild, measure, regions, validate BCs on the procedural beam
├── geometry/<part>/            # exported STEP/STL, regions.json, keep.stl (gitignored)
├── runs/<part>/<run_id>/       # config snapshot, .mechdat, opt.stl, final.stl, islands.stl,
│                               # mechanical.json, rebuild.json, metrics.json, validation.json, logs
└── results/
```

**Conventions.**

- **Units:** millimetres in all files. Check the scale of the Ansys STL on the first export, since Mechanical may write it in SI units.
- **Secrets:** Onshape API keys come from environment variables and are never committed.
- **Run records:** each run saves a snapshot of its resolved config, the Ansys version, and wall times.

**Dependencies:**

- Ansys: `ansys-mechanical-core`, `ansys-mapdl-core`.
- Geometry: `trimesh`, `manifold3d`, `rtree`, `libigl` (point queries: winding numbers, distances).
- Validation meshing: `ansys-meshing-prime` (PyPrimeMesh, matched to the Ansys release). It writes the CDB that MAPDL reads. TetGen and gmsh were tried and dropped in Phase 3 (see Section 13).
- General: `numpy`, `scipy`, `pyyaml`, `pytest`, `matplotlib`.

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
- [x] If the settings don't change after Gate 2, these two runs count toward the matrix. (Re-run on 24 cores for comparable timings.)

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

- [x] Run the core matrix (Section 5), then the extras chosen at Gate 2. (M0 at h = 1 is optional and wasn't run.)
- [x] Process each run: measure M0 runs on their raw STL; for M0R, M3, and M2, run rebuild → measure → validate. Write all artifacts to `runs/<run_id>/`.
- [x] `collect.py` writes `results/summary.csv` and these plots:
  - bore error vs h, by method,
  - specific stiffness vs TO wall time,
  - volume bookkeeping per run.
- [x] Write `results/REPORT.md` covering:
  - pass/fail against Section 9,
  - predictions vs outcomes (Section 10),
  - a recommendation for production use.

### Phases 5–8 — General geometry

The beam code assumes Y-axis bores at coordinates listed in `base.yaml`. Every beam-specific step goes through `config.bores()`: `geometry_check`, the rebuild probes, `measure`, `run_mechanical.bore_selection` / `zone_x_range`, and `validate`'s `radial_to_axes`, `bore_nodes` and hard-coded `pilot`. Phases 5–8 replace that with regions read from the model, keeping the method itself unchanged: M3 exclusion, `final = (opt ∪ keep) ∩ space`, and validation of the rebuilt part.

**Region definitions** (used by every phase below). For a region with keep solid K and a small tolerance ε (`regions.surface_tol`, about 0.01 mm):

- **Exclusion faces S:** the parts of `SPACE`'s surface that lie strictly inside K (signed distance > ε). They carry the M3 exclusion and the BCs. On the beam these are the bores.
- **Preserved surface P:** the parts of `SPACE`'s surface inside or on K (≥ −ε). This is everything the rebuild reproduces exactly. P contains S plus flush areas such as the beam's seat annuli.

### Phase 5 — Preserve region feature, export and checks

**FeatureScript.** Written to the working document through the Onshape MCP (which uses Josh's API allocation; tell him before starting).

- [x] `featurescript/PreserveRegion.fs`, Feature Studio `PreserveRegion`. Parameters:
  - faces (on one part),
  - role (FIX / LOAD / PASSIVE),
  - label (A–Z, 0–9, `_`; default `1`),
  - wall thickness t into the material (this is also the Exclusion Thickness),
  - void margin m into the void (default 0.5 mm).
- [x] Build the keep with `opThicken`: t into the material and m into the void (verified: `thickness1` runs along the outward face normal, into the void, so it takes m; `thickness2` takes t). Make one body per connected face group, named `KEEP_<ROLE>_<label>`, or `KEEP_<ROLE>_<label>_<k>` when there are several.
- [x] The void margin is required. The keep must straddle every preserved face so that the face in `final` comes only from `SPACE`'s tessellation (Section 1). A keep face lying on a bore would put two tessellations of the same curved surface into the boolean. The beam's solid cylinders straddle by construction.
- [x] Attach an attribute to each keep body: role, label, t, m, the selected face count and their total area. The exporter reads it, and the face matching in Phase 6 is checked against it.
- [x] Fail in the feature with a clear message when faces span more than one part, when the label is invalid, or when the thicken fails (for example m ≥ a bore radius).
- [x] `TopoTestBeam`: add a `createKeeps` boolean (default true) so the beam can also be marked with the new feature.

**Modelling guide.**

- [x] `docs/modelling.md`, for Josh and teammates:
  - model `SPACE` with every small feature cut in;
  - split a face to preserve only part of it (for example a washer seat);
  - use one Preserve region per functional interface;
  - how to choose t (the wall that must stay solid; M3 used the keep wall, 3.75 mm) and m;
  - mistakes to avoid, such as FIX and LOAD keeps touching.

**Test geometry.**

- [x] **Beam, re-marked:** a second Part Studio with `TopoTestBeam` (`createKeeps` = false) and Preserve region on the 8 bores (t = 3.75 mm, m = 0.5 mm).
- [x] **Bracket:** `featurescript/TestBracket.fs`, an L-bracket that covers what the beam doesn't: holes on two non-parallel axes, a counterbored hole, a planar mating pad (split face), and a slot. Josh instances it and marks it with Preserve region in the GUI, which also tests the workflow. *(Done 2026-10-20: Josh marked it with the defaults and found the feature intuitive.)*

**Export (`onshape_export.py --part <part>`).**

- [x] Per-part config `config/parts/<part>.yaml`: Onshape IDs, material, supports and loads by region name, TO overrides, and t for any hand-modelled keeps. The beam moves to `config/parts/beam.yaml` (plus `beam_marked.yaml`); `base.yaml` keeps only the shared settings.
- [x] Find `SPACE` and every `KEEP_*` by name; don't take the list from the config.
- [x] Read the keep attributes through the FeatureScript evaluate endpoint (verified), and write each region's role, label, t, m, face count and area to `geometry/<part>/regions.json`. A hand-modelled keep without an attribute takes t from the part YAML; if there's no t there either, fail.
- [x] Get `SPACE`'s exact volume from Onshape's mass properties (verified: the body comes back keyed `-all-`, in m³).

**Checks (`geometry_check.py`, generic).** All must pass:

- [x] The `SPACE` STL volume is within ±0.1 % of the Onshape mass-properties volume. This replaces the beam's analytic formula.
- [x] All STLs are watertight volumes.
- [x] Names are unique and well formed. There is at least one FIX and one LOAD region, and every region the part YAML names exists.
- [x] Each keep straddles its faces: K ∩ `SPACE`, K − `SPACE` and S are all non-empty.
- [x] Per region, the area of S measured on the STL matches the attribute's face area within 1 % (tessellation tolerance).
- [x] No FIX keep overlaps a LOAD keep.
- [x] `keep.stl` is the union of all `KEEP_*` parts, as one manifold.
- [x] The original beam (hand-modelled keeps, t from YAML) passes.

**Gate 3:** Josh reviews the feature in the GUI on both parts (how it feels to use, names, error messages), `docs/modelling.md`, and the `geometry_check` output.

### Phase 6 — Generic pipeline and regression

**Mechanical (`run_mechanical.py`).**

- [ ] Face matching: sample points on every CAD face of `SPACE` (the face's mesh nodes after meshing *(verify the MeshData API)*). Assign a face to a region when none of its points is outside K by more than ε and most are strictly inside. A face that matches two regions is an error.
- [ ] Per region, the matched face count and area must equal the attribute's (area within 0.1 %).
- [ ] Build one named selection per region (`NS_<ROLE>_<label>`) from the matched face IDs. This replaces the radius worksheet.
- [ ] BCs from the part YAML: fixed supports on FIX regions, and one remote force (vector, behaviour) per load entry on its LOAD regions. One static load case; several load cases are out of scope for now.
- [ ] M3 exclusion on all S faces, with Exclusion Thickness = each region's t. If regions differ in t: can Mechanical take several exclusion regions, each with its own thickness? *(verify)* If not, fail with a message asking for one t per part.
- [ ] Keep M0 (All Boundary Conditions, 2h) for comparison runs.

**Rebuild (`rebuild.py`).** Input checks and the boolean are already generic.

- [ ] Replace the bore probes with overlap volumes: each region's K ∩ `SPACE` belongs to the component of `final` that holds most of its volume. The main body holds every FIX and LOAD region. Failure rules are unchanged.

**Metrics (`measure.py`).** Volume bookkeeping, stiffness and cost stay as in Section 9.

| Metric | How | Pass |
|---|---|---|
| Preserved-surface deviation | Per region: sample P at about 0.1 mm spacing, then take the distance from each sample to the surface of `final` (or raw `opt`). Report the max, the 99th percentile, and the fraction of samples beyond the tolerance. | ≤ 0.02 mm with no samples beyond, for rebuilt runs |
| Connectivity, PASSIVE attachment | From the rebuild report | As in Section 9 |
| Seam necks | Distance bands outside each K inside `SPACE`, in 0.5 mm steps up to the smaller of 2h and half the gap to the nearest other keep. Per band: the fraction of `SPACE`'s band volume that `final` keeps (grid sampling). | Flag dips > 25 % |

- [ ] Implement these metrics. On the beam, the bands are the existing cylindrical shells, so the seam results should match the old metric.
- [ ] Keep the bore-fit and seat metrics running on the beam until Gate 4 so the two can be compared.

**Validation (`validate.py`).**

- [ ] BC nodes: FE nodes within the tolerance (rename `fix_radial_tol` to `bc_surface_tol`) of the S triangles of the FIX and LOAD regions.
- [ ] Read the pilot location and force vector from the run's `mechanical.json`, not `validate.pilot`.
- [ ] Converge on pilot displacement along the load direction.
- [ ] Measure the stress exclusion bands as distance to K. Stresses stay indicative only.
- [ ] Rename `bore_size` to `feature_size`; Prime's curvature sizing is already generic.

**Layout and tests.**

- [ ] Use `geometry/<part>/`, `runs/<part>/<run_id>/` and `config/parts/`; `run_matrix` and `collect` take `--part`. Update Section 6 and the README.
- [ ] Port the Phase 2 synth tests to the generic code, with the same planted defects and acceptance.
- [ ] Orientation test: apply an arbitrary rotation to a synth case (`space`, `keep`, `opt`); every metric must stay the same within its tolerance.

**Regression and first general runs.** Record them in `results/general_geometry.md`.

- [ ] Original beam through the generic pipeline, M3_h4 and M3_h2. Requirements:
  - face matching selects exactly the 8 bores;
  - element count, iterations and final volume match Phase 4;
  - specific stiffness is within 0.5 %;
  - fidelity passes.
- [ ] Re-marked beam, M3_h2: same tolerances, against the original-beam run.
- [ ] Bracket, end to end: M3 at h = 2 and 4 + rebuild + measure + validate, plus M0 at h = 2 to compare raw against rebuilt on off-axis holes.

**Gate 4:** Josh reviews the regression and the bracket results, decides whether to retire the beam-only metrics, and picks the settings for his own part.

### Phase 7 — Skill and plugin

- [ ] Project skill `.claude/skills/topo-rebuild/SKILL.md` (check the current skill format first). It covers:
  - the modelling contract, linking `docs/modelling.md`;
  - the command order, and where to stop for review (after `geometry_check`, and after the first TO run);
  - the recommended settings (M3 + rebuild at h = 2; validate the rebuilt part);
  - run times, and running solves in the background;
  - licences (take every HPC pack the pool grants; Prime and MAPDL share one seat) and the Onshape API allocation;
  - how to read `metrics.json`, `validation.json` and the report.
- [ ] Test it from a fresh session on the bracket: given only "run the bracket", the session should run the whole chain correctly.
- [ ] Plugin for teammates: the skill plus the Onshape MCP config, with setup notes (Ansys 2026 R1, Python 3.12 venv, API keys). Josh chooses where it's published.

### Phase 8 — Josh's first real part

- [ ] Josh models it following `docs/modelling.md`. The part YAML comes from Josh's description of the loads (Claude can draft it).
- [ ] Run it through the skill from a fresh session: M3 + rebuild at h = 2, then validate.
- [ ] Fix anything the skill or the guide got wrong.

If Josh wants results on his part before the skill exists, Phase 8 can start right after Gate 4.

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

Next session: superseded by the 2026-10-16 entry below.

Run artifacts (`runs/M3_h4`, `runs/M0_h4`, `runs/M0R_h4`) are local only (gitignored). Regenerate them with the commands at the end of `smoke_test.md`.

**2026-10-16 — Phase 3 done; at Gate 2.** `results/smoke_test.md` is final for Gate 2.

- Validation meshing now goes through the **Ansys Prime wrapper** (PyPrimeMesh 0.10.5), replacing pymeshlab + TetGen. The TetGen hang guard (`360dda9`) worked, but TetGen still failed 5 of 5 attempts on M0R and the full body: the validation surfaces self-intersect (38 faces on M0R) and are full of slivers. TetGen `-Y`, gmsh, and Prime meshing the surface as given all fell short too. `prime_mesh` wraps `final.stl` directly, checks the wrap for self-intersections, meshes quadratic tets, and exports a CDB that MAPDL reads with `CDREAD`. The solver is PCG.
- **Results:** M3_h4 converged (pilot UZ −1.01468 mm, 0.15 % from the TetGen-based result). M0R_h4 converged (−1.25562 mm). The full-body BC check is within 0.42 % of Mechanical. M3 has 19 % higher specific stiffness than M0R, and its model-form gap is +9.7 %.
- **Licensing:**
  - Prime and MAPDL use the same `ansys` seat, and the pool grants one. So each level meshes, closes Prime, then starts MAPDL.
  - MAPDL tries 24, then 12, then 4 cores (`validate.nproc`). Josh wants as many HPC packs as are free. Two packs were granted on 2026-10-16; on 10-15 only one was.
- **Stresses are not mesh-converged:** M3's member peak is 56 vs 32 MPa between meshers. This is a Gate 2 decision.

Gate 2 questions for Josh (details in `smoke_test.md`): the premature volume stop (Q7), normalization by mass, h = 1 vs 1.5 (run `M3_h2` first), extras (X2), and how to treat stresses.

**2026-10-16 — Gate 2 passed. Josh's decisions:**

1. **Early volume stops are acceptable.** Convergence settings stay unchanged.
2. **Normalize by stiffness per unit mass**, but it is a secondary metric. **The main objective is preserving the small features**: bore and seat fidelity, connectivity, and seams (Section 2, Q1–Q2). Payoff (Q3) is supporting evidence, so REPORT.md should lead with fidelity.
3. **Run `M3_h2` first as the timing probe**, then choose h = 1 or 1.5 for the reference.
4. **Run extra X2** (`M3_h4_X2`, Exclusion Thickness 8 mm).
5. **Stresses are indicative only.** No stress pass/fail and no stress convergence check.

Phase 4 setup:
- TO solves now also try 24, 12, then 4 cores (`mechanical.nproc`), and record the cores used in `mechanical.json`.
- The h = 4 runs are re-run on that setting so wall times are comparable. The 4-core originals are kept in `runs/_h4_4core/`.
- `scripts/run_matrix.py` chains solve → rebuild → measure → validate per run, and handles M0 → M0R.


**2026-10-16 (afternoon) — Phase 4 batch 1 done** (`scripts.run_matrix M3_h4 M3_h2 M0_h4 M3_h4_X2 M0_h2`, every step ok):
- **Fidelity (main objective):** every rebuilt run (M3, M0R, X2, at h = 4 and 2) has bore radius error ≤ 0.0002 mm, seat deviation 0, no misses, FIX–LOAD connected, both PASSIVE attached, and no seam flags. Raw M0 misses badly: at h = 4, 0.62 mm bore error, 11.3 mm seat deviation and 92 misses; at h = 2, 0.10 mm, 3.9 mm and 0 misses.
- **Specific stiffness (N/mm/kg):** M3 1 463 (h4) and 1 616 (h2); M0R 1 227 and 1 576; X2 1 243. Model-form gap: M3 +9.9 % at h = 4, +1.3 % at h = 2.
- **X2 (8 mm exclusion) hurts:** it stops after 3 iterations, like M0, and gives 15 % lower specific stiffness than M3_h4. 3.75 mm holds below one element.
- **Cores don't change results:** M3_h4 on 24 cores is within 0.14 % of the 4-core run.
- **Timing probe:** M3_h2 has 80 242 elements, a 211 s TO solve and 18 iterations, on 24 cores with 1.7 GB of MAPDL memory. Projected h = 1: about 640 k elements, roughly 20–40 min and about 14 GB, so the h = 1 reference is affordable. **Reference = h = 1** (`M3_h1`), started 2026-10-16.

**2026-10-16 (evening) — Phase 4 done.** M3_h1 (reference) has 428 171 elements and took a 385 s TO solve on 24 cores; its features are exact, and its specific stiffness is 1 654 N/mm/kg. `pipeline/collect.py` writes `results/summary.csv` and three plots. `results/REPORT.md` leads with feature preservation: every rebuilt run is exact, and raw M0 is not. Its recommendation is M3 + rebuild at h = 2 (−2.3 % vs reference at half the TO time). M3_h4 misses the 10 % payoff criterion by 1.6 points.

**2026-10-20 — Phases 5–8 planned (general geometry).** Josh wants to use the method on more complex parts that he models himself in Onshape. The agreed decisions are at the end of Section 3; the work is Phases 5–8 in Section 7.

- **Start at Phase 5** in a new session. The Onshape MCP is needed for the FeatureScript; tell Josh that it uses his API allocation.
- **Not decided yet:**
  - the exact layout (`config/parts/`, `geometry/<part>/`, `runs/<part>/`) is a proposal;
  - whether Mechanical can carry several exclusion thicknesses (Phase 6);
  - where the plugin is published (Phase 7).
- **Phase 4 run artifacts** stay under `runs/<run_id>/`. Move them to `runs/beam/` when the layout changes, so the regression can read them.

**2026-10-20 — Phase 5 built; Gate 3 waits on the bracket marking.** Commits `a6520d6`, `7bbb981`.

- **Onshape document now holds:**
  - Feature Studios `PreserveRegion` (`4778f12f98e205100bf31df6`), `TestBracket` (`73355875827b9bb43e74f4f1`), and `TopoTestBeam` with the new `createKeeps` switch.
  - Part Studios "Beam marked" (`4ed834e6af017d62bfa18f73`) and "Bracket" (`71998051ac301addb3814c18`).
  - An empty duplicate `PreserveRegion` studio (`f1ed77ae…`) left by an MCP call that reported failure but went through. The API keys can't delete elements; Josh deletes it in the GUI.
- **Results:** `beam` (hand-modelled keeps) and `beam_marked` pass every generic check. The S areas are within 0.005 % of the feature's face areas. The Onshape mass-properties volume equals the Phase 1 analytic volume, and `beam` also passes the legacy analytic checks.
- **Layout:** new exports go to `geometry/<part>/`. Phase 1–4 code still reads `geometry/` and the beam keys in `base.yaml`, and `geometry_check` without `--part` keeps the old behaviour. Phase 6 does the move.
- **Region model:** a region is `<ROLE>_<label>`. A Preserve region feature that selects several separate face groups makes one region with keeps `_1.._k`, so the marked beam has FIX_1 (4 keeps), LOAD_1 and PASSIVE_1, while the hand-modelled beam has FIX_1..4 and so on.
- **Facts learned:**
  - `getProperty(NAME)` isn't available while a feature regenerates (it came back empty), so the feature can't check that its faces are on `SPACE`; `geometry_check` catches that case instead.
  - S area is measured by per-triangle quadrature, not subdivision. Onshape's planar faces are long slivers, and subdividing them exploded to millions of triangles. Region boundaries fall on triangle edges because Onshape tessellates each face separately.
  - The STEP translation sometimes takes about 9 minutes on Onshape's side.
- **Environment:**
  - Smart App Control blocks uv's venv launcher. `.venv` was rebuilt with the stdlib `python -m venv --upgrade`; don't run `uv venv` on it.
  - The Onshape MCP session expired mid-session and showed up as "unexpected error". REST with the API keys works as a fallback for writing Feature Studios (`POST featurestudios/...` with `sourceMicroversion`) and placing features.
- **Next:** Josh marks the bracket (suggested: FIX on the two base holes plus the counterbored hole, LOAD on the upright holes and the mating pad, PASSIVE on the slot; t = 3 mm suits the 8 mm plates). Then run `onshape_export --part bracket` and `geometry_check --part bracket`, settle the bracket's loads in `config/parts/bracket.yaml`, and hold **Gate 3**.

**2026-10-20 (later) — Bracket marked and checked; at Gate 3.** Commit `c95f32e`.

- Josh marked the bracket with the defaults (t = 3.75, m = 0.5): FIX_1 on the two plain base holes, LOAD_1 on the two upright holes, PASSIVE_1 on the slot (4 faces), and PASSIVE_2 on the counterbored hole's lower bore and step face. The counterbore wall wasn't selected. He confirmed 150 N in +X as the test load. All checks pass (S areas within 0.012 %).
- **His two PASSIVE regions both used label `1`,** which gave two parts named `KEEP_PASSIVE_1`. The feature now refuses a role + label already used by another Preserve region; it finds them through the attributes and suggests a free label. Claude relabelled the second one to `2`.
- **The feature name template shows "Preserve <Role> <label>".** Features created before the change keep their stored names ("Preserve region N") unless Onshape shows the template name; Josh to confirm in the GUI.
- **Josh's request:** anything to delete gets "(delete me)" appended. The empty duplicate studio is now "PreserveRegion (delete me)", renamed with `POST metadata/d/{did}/w/{wid}/e/{eid}` (Name property).

**2026-10-20 (later) — Preserve region labels are automatic.** The label defaults to empty, meaning the lowest number not yet used by an earlier region of the same role; a typed label is still validated and must be unique. The tree name comes from the template "Preserve <Role> <label>". Josh couldn't rename the features (a template-named feature takes its name from the template), and the existing ones kept their stored "Preserve region N" names. The bracket and Beam marked features now have automatic labels and empty stored names (set over REST); their parts are unchanged. Josh to confirm the tree shows "Preserve Fix 1" and so on.

**2026-10-20 — Gate 3 passed.** Josh confirmed that the feature tree shows the template names, the feature is intuitive to use, and the bracket's test load is 150 N in +X. He asked for the naming fixes before Phase 6; they're done.

Phase 6 starting points:
- Run Phase 6 in a new session. Read the Phase 6 checklist and the Phase 5 entries above first.
- `geometry/<part>/` holds verified exports for `beam`, `beam_marked` and `bracket` (gitignored). Re-create one with `python -m pipeline.onshape_export --part <part> && python -m pipeline.geometry_check --part <part>`. The STEP step can take about 10 minutes.
- `pipeline/regions.py` has the Region model and `surface_in_keep`, ready for face matching and the generic metrics.
- Phase 4 run artifacts are still under `runs/<run_id>/`. Move them to `runs/beam/` when the layout changes.
- Python: use `.venv/Scripts/python.exe` (stdlib launcher) and `python -m pytest`.
