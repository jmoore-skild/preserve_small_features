# Phase 3 smoke test: `M3_h4` and `M0_h4`

**Status: draft.** Validation of M0R_h4 and the BC check are pending (see Open items).
**Date:** 2026-10-15. **Ansys:** 2026 R1 (v261), Mechanical Enterprise (shared web licence), embedded PyMechanical 0.13.4; validation in MAPDL 2026 R1 via PyMAPDL 0.74.1.
**Run artifacts:** `runs/M3_h4`, `runs/M0_h4` (raw), `runs/M0R_h4` (post-only rebuild of `M0_h4/opt.stl`). Not committed; regenerate with the commands at the end.

## Summary for Gate 2

- The full chain runs unattended: STEP → Mechanical → level-set TO → STL → rebuild → measure → validate.
- Both h = 4 runs solve in about 45 s of TO time. The exported level-set STL is watertight, in mm, in the CAD frame, and needs no repair.
- After the rebuild, bores and seats are exact for both M3 and M0R (radius error 0.0002 mm, seat deviation ≤ 5e-6 mm). Raw M0 is off by 0.62 mm at the bores and has 92 eroded seat samples.
- **Decisions needed at Gate 2:**
  1. **The optimizer stops below the volume target.** M3 stops at 59.2 % and M0 at 57.0 % of the original volume (target 60 %), after a run of rejected iterations. See Q7 and the settings section.
  2. **Normalization.** Excluded volume counts toward the 60 %, and final volumes differ by method, so compare by stiffness per mass (Section 9 already does).
  3. **h = 1 vs 1.5.** h = 4 is dominated by fixed overhead and can't be extrapolated. I recommend running `M3_h2` first as the timing probe (Q9).
  4. **Extras.** X2 looks informative (Q6). X3 isn't needed for the core study.

## Answers

**Q1. Level-set STL export from scripting.**
Use `TopologyDensity.AddSmoothing()` and then `Solution.EvaluateAllResults()`. The STL path is the Smoothing object's read-only `FileName` property, inside the project's `_Mech_Files\StructuralOptimization\` folder. `run_mechanical.py` copies it to `runs/<id>/opt.stl`.

This is the same route as density-based. The difference is the Smoothing **Move Limit**, which defaults to 0 for level-set: no smoothing, so the exported facets are the level-set surface itself. The solution's own "Export Optimal Shape: Only Geometry" output (`shape.out`) is only a 3.6 mm voxel grid and isn't usable.

The Section 5 smoothing sweep is therefore possible (Move Limit > 0), but nothing needs it: the raw export is already watertight. `topology_optimization.export_smoothing_move_limit` in `base.yaml` holds it at 0.

**Q2. Location, units, frame, watertightness.**
The file is written to the project's `_Mech_Files` folder (copied out, see Q1) in mm and in the global CAD frame. Bounds are x 3.9–196, y 0–15, z 0–60. It is a single body, watertight and consistently wound (`is_volume`), with 9 672 triangles for M3.

The facets are of mixed quality: some edges are near zero length and 1 028 faces have a minimum angle under 10°. That's fine for the booleans, but it matters for tet meshing (see Validation).

**Q3. Deformable remote force with level-set.** Accepted. Both runs solved with the remote force at Behavior = Deformable.

**Q4. Does `SPACE` arrive as the part name?** Yes. The single body arrives as `SPACE|SPACE` (part `SPACE`).

**Q5. Does the radius worksheet select exactly the 8 bores?**
Yes. The criterion is Face / Radius / Equal / 2.25 mm and it selects exactly 8 faces. Per-zone selections add a Location X range filter (the zone's bore x ± keep radius): FIX gets 4 faces, LOAD gets 2.

**Q6. Is the manual Exclusion Thickness honored?**
Approximately, at h = 4. The table shows the fraction of each Ø12 keep annulus (keep ∩ SPACE) that the raw `opt.stl` already fills:

| | FIX 1–4 | LOAD 1–2 | PASSIVE 1–2 |
|---|---|---|---|
| M3 (bore NS, 3.75 mm) | 91.7 / 96.8 / 92.1 / 97.7 % | 89.3 / 90.4 % | 99.9 / 98.0 % |
| M0 (all BCs, program controlled) | 100 % ×4 | 100 % ×2 | 80.7 / 64.3 % (unmarked) |

The API reads back 3.75 mm for M3. The frozen region is element-based, so with 4 mm elements its boundary cuts chords inside the Ø12 circle. The rebuild's `∪ keep` restored 2 020 mm³ for M3, which is exactly the shortfall the method is designed to absorb.

M0's program-controlled thickness reads back as 0 mm through the API, but it evidently covers at least the keep radius (expected 2h = 8 mm). This is the comparison X2 would quantify.

**Q7. Does excluded volume count toward the 60 % target?**
Yes. The volume constraint's iteration-0 value is 178 134 mm³, the whole body including exclusions, and the limit is 106 880 mm³ (60 %).

Neither run reached the limit:

- **M3:** 105 389 mm³ (59.2 %) after 9 accepted iterations;
- **M0:** 101 515 mm³ (57.0 %) after 3.

`topo.out` shows the optimizer ending after several rejected iterations with the constraint inactive. The Topology Density result reports 58.8 % and 56.8 % (element-based measure).

So final mass differs by method, and the comparison must be by stiffness per mass, as Section 9 already does. Whether to tighten convergence is a Gate 2 question (see Settings).

**Q8. `InitializationHoleSize` / `InitializationModifierType`.**
The defaults are 0 mm and `None`. Neither is shown in the details view for level-set, and neither is mesh-relative, so nothing needs pinning. Both are recorded per run in `mechanical.json` (`topology_setup.region_api`).

**Q9. Solve time, and h = 1 vs 1.5.**

| | elements | nodes | static solve | TO solve | TO iterations |
|---|---|---|---|---|---|
| M3_h4 | 16 860 | 27 271 | 20.6 s | 43.9 s | 9 (16 incl. rejected) |
| M0_h4 | 16 860 | 27 271 | 21.2 s | 39.1 s | 3 |

Each FE solve inside the TO loop is 81 825 equations and takes under 1 s of CPU (distributed PCG). The h = 4 wall time is almost entirely fixed overhead (MAPDL start-up, per-iteration I/O and level-set processing), so it can't be extrapolated.

Elements scale about 8× per halving of h: roughly 135 k at h = 2, 1.1 M at h = 1 and 320 k at h = 1.5. **Recommendation:** run `M3_h2` (a core-matrix run) first and take the scaling from h = 4 → 2 before committing to h = 1.

**Q10. Can the optimizer's final compliance be retrieved?**
Yes. `ObjectiveHistory.out` in the solver directory has the objective and the volume per iteration; `run_mechanical.py` saves it to `objective_history.json` and the summary to `mechanical.json`.

**The objective is strain energy, ½F·u, not F·u.** Iteration 0 gives 75.263, and the full-design static solve gives a LOAD-bore UZ of −0.7526 mm, so ½ × 200 N × 0.7526 mm = 75.26. The model-form gap must use `validation.json`'s `strain_energy`, not `compliance`.

Final values: M3 92.511, M0 111.135 N·mm.

**Q11.** Not applicable (X3 not planned yet).

## Post-processing results (h = 4)

| Run | V_final mm³ | added | trimmed | islands | bore r-err max | seat dev max | seat misses | FIX–LOAD | PASSIVE | seam flags |
|---|---|---|---|---|---|---|---|---|---|---|
| M3_h4 | 105 918 | 2 020 | 1 908 | 0 | 0.0002 | 0.0000 | 0 | connected | attached | none |
| M0R_h4 | 102 243 | 2 277 | 1 908 | 0 | 0.0002 | 0.0000 | 0 | connected | attached | none |
| M0_h4 (raw) | 101 873 | – | – | – | 0.62 | 11.3 | 92 | connected | attached | none |

"Trimmed" equals the 8 bore volumes exactly: at h = 4 the level-set closes every Ø4.5 bore, so all bore geometry comes from the rebuild.

## Validation (`validate.py`)

**Status:** M3_h4 is done and converged. M0R_h4 and the full-body BC-equivalence check are **pending** (see Open items).

| Run | level | global / bore size mm | tets | nodes | pilot UZ mm | change | mesh s | solve s |
|---|---|---|---|---|---|---|---|---|
| M3_h4 | 0 | 1.5 / 0.4 | 623 332 | 1 015 880 | −1.01308 | – | 22 | 109 |
| M3_h4 | 1 | 0.75 / 0.2 | 1 956 110 | 2 894 907 | −1.01621 | 0.31 % | 53 | 1 273 |

M3_h4, from the converged level:

- **Compliance and stiffness:** F·u = 203.24 N·mm; strain energy ½F·u = 101.62 N·mm; stiffness 196.8 N/mm.
- **Mass:** V_final 105 918 mm³, which gives 0.1345 kg and a specific stiffness of 1 463 N/mm/kg.
- **Model-form gap:** validated strain energy 101.62 vs the optimizer's final 92.51, so **+9.8 %**. The rebuilt part is softer than the optimizer's own model claimed. Possible contributors: the coarse h = 4 quadratic-tet TO model being stiff, the level-set boundary cutting through elements, and the bores reopened by the rebuild. The full-body check (pending) will separate BC differences from the rest.
- **Stresses:**
  - members: max von Mises 31.9 MPa, excluding material within 2 mm of the keep zones;
  - bores: FIX 8.8–14.2, LOAD 10.8–12.0, PASSIVE 3.9–5.3 MPa;
  - seams (1 mm outside the keep boundary): 2.4–4.8 MPa.
- **Cost:** about 25 min per validated run, dominated by the level-1 solve (≈ 8.7 M DOF).

**Implementation notes.** These depart from the plan's tooling; the method itself is unchanged.

- The tet-mesh input is the same rebuild, `(opt ∪ keep) ∩ space`, but against a **remeshed SPACE** (pymeshlab isotropic remesh: global size, refined to the bore size within 1.5 R of each axis, feature edges kept, ≤ 0.005 mm deviation). The resulting volume matches `final.stl` to 0.005 %.
  - The Onshape STL triangulates the flat faces as long slivers fanning out from the bore circles. Meshed directly, `final.stl` gave 6 M tets at the first level.
  - Remeshing `opt` or `final.stl` instead created self-intersections in thin members, so only SPACE (no thin walls) is remeshed. The remesh is checked for self-intersection and retried, because pymeshlab's result varies between calls.
- TetGen 0.8.4 segfaults when given a background sizing mesh, and its Python `maxvolume` keyword is ignored. The volume cap goes in as a raw switch string (`pq2.0a<0.3·g³>o2`), and sizes grade in from the surface.
- The radius-edge bound is 2.0, not 1.5: the remaining slivers at the boolean seams otherwise cascade into millions of tets.
- Seam stress is the max nodal von Mises over nodes within ±0.25 mm of radius R + 1 mm, not interpolated probes. Windows Smart App Control blocks VTK's `vtkFiltersVerdict` DLL on this machine, so VTK cell filters (quality, sizes, probe) are avoided.
- The remote-force pilot defaults to the LOAD-face centroid (190, 7.5, 30), which confirms the Section 9 *(verify)*. Validation uses RBE3 to that pilot (UX/UY/UZ).

## Open items (next session)

1. **TetGen robustness.**
   - TetGen has failed three ways on these surfaces:
     - internal errors (`split_subface`, `recoversubfaces`), now caught and retried on a fresh remesh;
     - segfaults, but only with a background mesh, which is no longer used;
     - a **hang**: the full-body check sat 74 min in TetGen at 100 % of one core and was stopped.
   - Fix: run `tet_mesh` in a child process with a timeout (normal meshing is 20–60 s) and treat a timeout like an error, i.e. retry on a new remesh.
2. **BC-equivalence check** (`python -m scripts.check_full_space`): validate the unoptimized SPACE body with the same chain and compare pilot UZ with Mechanical's full-design static result, −0.7526 mm (LOAD-bore average).
3. **Validate M0R_h4.**
4. Finish this file and the Gate 2 summary with those numbers.

## Settings for Gate 2

- **Premature stop (Q7).** Options:
  - accept it, since normalizing by mass absorbs it;
  - tighten Convergence Accuracy (default 0.1 %) or raise the move limit, and re-run h = 4 to see whether volume reaches 60 %.

  I'd test one tightened setting at h = 4 before Phase 4, because M0 stopping after 3 iterations makes its topology hard to compare.
- **Element order** is Program Controlled, which gives quadratic tets (27 271 nodes for 16 860 elements). Mesh sizing is uniform (adaptive sizing, curvature and proximity off); method All Tet, Patch Conforming.
- **Material** is cloned from Ansys's *Polyethylene* entry with E, ν and ρ overwritten. The reported mass (0.1331 kg for 104 810 mm³) confirms ρ = 1270 kg/m³.

## Environment notes

- Embedded Mechanical exits with "legacy Provider loading failed" unless Ansys's `libcrypto-3-x64.dll` is loaded before Python's. `run_mechanical.py` preloads it.
- New dependency: `pymeshlab` (validation remesh).

## Reproduce

```powershell
python -m pipeline.run_mechanical --run M3_h4
python -m pipeline.run_mechanical --run M0_h4
python -m pipeline.rebuild  --run-dir runs/M3_h4;  python -m pipeline.measure --run-dir runs/M3_h4 --h 4
python -m pipeline.measure  --run-dir runs/M0_h4 --h 4 --raw
# M0R_h4: copy runs/M0_h4/opt.stl into runs/M0R_h4, then rebuild + measure as for M3
python -m pipeline.validate --run-dir runs/M3_h4
python -m pipeline.validate --run-dir runs/M0R_h4
```
