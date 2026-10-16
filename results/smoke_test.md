# Phase 3 smoke test: `M3_h4` and `M0_h4`

**Status: final.** Gate 2 passed on 2026-10-16. Decisions are in the plan, Section 13: early stops accepted; small-feature preservation is the main objective, with stiffness per mass secondary; `M3_h2` first; X2 added; stresses indicative only.
**Date:** 2026-10-15/16. **Ansys:** 2026 R1 (v261), Mechanical Enterprise (shared web licence), embedded PyMechanical 0.13.4; validation meshed in Ansys Prime 2026 R1 via PyPrimeMesh 0.10.5 and solved in MAPDL 2026 R1 via PyMAPDL 0.74.1.
**Run artifacts:** `runs/M3_h4`, `runs/M0_h4` (raw), `runs/M0R_h4` (post-only rebuild of `M0_h4/opt.stl`). Not committed; regenerate with the commands at the end.

## Summary for Gate 2

- The full chain runs unattended: STEP → Mechanical → level-set TO → STL → rebuild → measure → validate.
- Both h = 4 runs solve in about 45 s of TO time. The exported level-set STL is watertight, in mm, in the CAD frame, and needs no repair.
- After the rebuild, bores and seats are exact for both M3 and M0R (radius error 0.0002 mm, seat deviation ≤ 5e-6 mm). Raw M0 is off by 0.62 mm at the bores and has 92 eroded seat samples.
- **Validation:** M3_h4 and M0R_h4 both converge (0.20 % and 0.28 % change in pilot UZ). M3 is **19 % stiffer per unit mass** than M0R: 1 465 vs 1 227 N/mm/kg.
- **BCs are equivalent:** the full SPACE body through the validation chain is within **0.42 %** of Mechanical's full-design static. So M3's +9.7 % model-form gap comes from the TO model, not from BCs.
- **Validation meshing changed** from pymeshlab + TetGen to the Ansys Prime wrapper, because TetGen could not mesh these surfaces reliably (see Validation). On M3 the new path agrees with the old one to 0.15 %.
- **Decisions needed at Gate 2:**
  1. **The optimizer stops below the volume target.** M3 stops at 59.2 % and M0 at 57.0 % of the original volume (target 60 %), after a run of rejected iterations. See Q7 and the settings section.
  2. **Normalization.** Excluded volume counts toward the 60 %, and final volumes differ by method, so compare by stiffness per mass (Section 9 already does).
  3. **h = 1 vs 1.5.** h = 4 is dominated by fixed overhead and can't be extrapolated. I recommend running `M3_h2` first as the timing probe (Q9).
  4. **Extras.** X2 looks informative (Q6). X3 isn't needed for the core study.
  5. **Stress metrics.** Peak stresses aren't mesh-converged and depend on the mesher (see Validation). Either add a stress convergence check or report stresses as indicative only.

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

**Status:** done. All runs converge at level 1, and the BC-equivalence check passes.

| Run | level | global / bore size mm | tets | nodes | pilot UZ mm | change | mesh s | solve s |
|---|---|---|---|---|---|---|---|---|
| M3_h4 | 0 | 1.5 / 0.4 | 499 187 | 768 810 | −1.01270 | – | 37 | 73 |
| M3_h4 | 1 | 0.75 / 0.2 | 2 352 354 | 3 326 008 | −1.01468 | 0.20 % | 93 | 223 |
| M0R_h4 | 0 | 1.5 / 0.4 | 523 700 | 813 746 | −1.25207 | – | 33 | 68 |
| M0R_h4 | 1 | 0.75 / 0.2 | 2 339 093 | 3 328 187 | −1.25562 | 0.28 % | 104 | 226 |
| SPACE (full body) | 0 | 1.5 / 0.4 | 496 579 | 706 492 | −0.75570 | – | 23 | 62 |
| SPACE (full body) | 1 | 0.75 / 0.2 | 3 652 422 | 5 017 077 | −0.75579 | 0.01 % | 141 | 353 |

All solves ran on 24 cores (base licence + 2 HPC packs). Mesh volumes match the input STLs to within 4e-4.

From the converged level:

| | M3_h4 | M0R_h4 |
|---|---|---|
| V_final mm³ / mass kg | 105 918 / 0.1345 | 102 243 / 0.1298 |
| stiffness N/mm | 197.1 | 159.3 |
| **specific stiffness N/mm/kg** | **1 465** | **1 227** |
| strain energy ½F·u N·mm | 101.47 | 125.56 |
| optimizer's final strain energy N·mm | 92.51 | 111.14 (M0's, before the rebuild) |

- **M3 vs M0R:** M3 is 19 % stiffer per unit mass. M0R is 3.5 % lighter but 19 % less stiff.
- **BC equivalence:** the unoptimized SPACE body through the same chain gives pilot UZ −0.75579 mm against Mechanical's −0.75264 mm LOAD-bore average (strain energy 75.58 vs 75.26), a **0.42 %** difference. The validation BCs (fixed FIX bores, RBE3 pilot) reproduce Mechanical's model.
- **Model-form gap (M3):** validated 101.47 vs the optimizer's 92.51, so **+9.7 %**. With BCs equivalent, the gap comes from the TO model: the coarse h = 4 elements and the level-set boundary cutting through them. It's an h = 4 number, and should shrink with h in Phase 4.
- **Cross-check with the old meshing path:** on M3 the TetGen-based validation (2026-10-15) gave −1.01621 mm against −1.01468 mm here, a 0.15 % difference. Level 1 now solves in about 4 min instead of 21.
- **Stresses (indicative only).** These are single-node maxima, and only pilot UZ is convergence-checked:

  | MPa | members (> 2 mm from keep) | FIX bores | LOAD bores | PASSIVE bores | seams |
  |---|---|---|---|---|---|
  | M3, Prime | 56.0 | 6.0–11.8 | 2.4–2.5 | 3.7–5.0 | 2.4–5.9 |
  | M3, TetGen (old) | 31.9 | 8.8–14.2 | 10.8–12.0 | 3.9–5.3 | 2.4–4.8 |
  | M0R, Prime | 168.6 | 6.0–11.5 | 1.6–1.8 | 4.7–5.1 | 1.6–5.0 |

  The two meshers disagree by up to 4× on the same part. The peaks sit at creases and re-entrant corners of the level-set surface, which are stress singularities, so they will keep rising with refinement. Stresses need a converged measure before Phase 4 relies on them (Gate 2 decision 5).

**Implementation notes.** These depart from the plan's tooling; the method itself is unchanged.

- **Meshing goes through the Ansys Prime wrapper.** For each level, `prime_mesh` wraps the STL (curvature sizing between the bore and global size, 30° feature recovery, at least 2 elements across gaps) and rejects the wrap if it self-intersects. It then fills it with quadratic tets and exports a CDB, which MAPDL reads with `CDREAD`. The STL is `final.stl` itself (or `space.stl` for the full-body check), so there is no remesh of SPACE and no boolean at validation time.
- **Why not TetGen:** the validation surfaces themselves were the problem. Prime's surface check found **38 self-intersecting faces** and 2 795 faces with skewness > 0.95 in M0R's pymeshlab-based surface. The Onshape STL has sliver fans, and the remesh had 5 µm edges. On these inputs:
  - TetGen crashed (`split_subface`, `recoversubfaces`, heap corruption), or hung past a 900 s timeout. That was 5 of 5 attempts on M0R and on the full body.
  - TetGen with `-Y` (boundary preserved) or gmsh Delaunay meshed some of them, but left thousands of near-flat tets (q < 0.01) or hung once the surface was subdivided.
  - Prime meshing the surface as given left near-flat tets, and its surface remesh still left 11 self-intersections.
- **Wrap accuracy:** volume within 4e-4 of the STL. Surface deviation is 0.03 mm at the 99th percentile, up to 0.7–0.8 mm at a few member creases about 26 mm from the nearest bore. Bore-surface nodes stay within 0.034 mm of the nominal radius, inside the 0.05 mm BC tolerance. The worst tet quality is 0.05–0.2, with no slivers.
- **Licensing:** Prime checks out the same `ansys` (Mechanical Enterprise) seat as MAPDL, and the shared pool grants one at a time. So each level meshes with Prime, closes it, then starts MAPDL for the solve. MAPDL tries 24, 12, then 4 cores and takes the first count whose HPC packs are granted (`validate.nproc`).
- **Solver:** PCG (tolerance 1e-8). The sparse direct solver ran out of core at level 1.
- Prime's CDB also contains wrap nodes that no element uses; BC selection skips them.
- Seam stress is the max nodal von Mises over nodes within ±0.25 mm of radius R + 1 mm, not interpolated probes. Windows Smart App Control blocks VTK DLLs on this machine (`vtkFiltersVerdict`, and the grid build in `ansys.mapdl.reader`), so the CDB is read as raw arrays.
- The remote-force pilot defaults to the LOAD-face centroid (190, 7.5, 30), which confirms the Section 9 *(verify)*. Validation uses RBE3 to that pilot (UX/UY/UZ).

## Open items

None for Phase 3. Gate 2 decisions are listed in the summary.

## Settings for Gate 2

- **Premature stop (Q7).** Options:
  - accept it, since normalizing by mass absorbs it;
  - tighten Convergence Accuracy (default 0.1 %) or raise the move limit, and re-run h = 4 to see whether volume reaches 60 %.

  I'd test one tightened setting at h = 4 before Phase 4, because M0 stopping after 3 iterations makes its topology hard to compare.
- **Element order** is Program Controlled, which gives quadratic tets (27 271 nodes for 16 860 elements). Mesh sizing is uniform (adaptive sizing, curvature and proximity off); method All Tet, Patch Conforming.
- **Material** is cloned from Ansys's *Polyethylene* entry with E, ν and ρ overwritten. The reported mass (0.1331 kg for 104 810 mm³) confirms ρ = 1270 kg/m³.

## Environment notes

- Embedded Mechanical exits with "legacy Provider loading failed" unless Ansys's `libcrypto-3-x64.dll` is loaded before Python's. `run_mechanical.py` preloads it.
- Validation dependency: `ansys-meshing-prime==0.10.5`, which matches Prime 2026 R1 at `v261/meshing/Prime`. `pymeshlab` and `tetgen` are no longer used.
- Licences come from Ansys shared web licensing. The local licence file expired on 2026-07-31, and the local vendor daemon is down.

## Reproduce

```powershell
python -m pipeline.run_mechanical --run M3_h4
python -m pipeline.run_mechanical --run M0_h4
python -m pipeline.rebuild  --run-dir runs/M3_h4;  python -m pipeline.measure --run-dir runs/M3_h4 --h 4
python -m pipeline.measure  --run-dir runs/M0_h4 --h 4 --raw
# M0R_h4: copy runs/M0_h4/opt.stl into runs/M0R_h4, then rebuild + measure as for M3
python -m pipeline.validate --run-dir runs/M3_h4
python -m pipeline.validate --run-dir runs/M0R_h4
python -m scripts.check_full_space
```
