# Reading a run's results

All files are in `runs/<part>/<run_id>/`. Units: mm, N, MPa, kg.

## mechanical.json (the TO run)

- `regions[]`: per region, the matched CAD `faces`, `face_count` and `area_mm2`, against the Preserve region
  attribute (`attribute_face_count`, `attribute_area_mm2`), and `ok`. Counts must be equal. Areas agree to
  within 1 %, because Mechanical's face area comes from its coarse display mesh (a bore reads about 0.16 % low).
- `unmatched_faces`: SPACE faces in no region. These are free to change.
- `loads[]`: per remote force, its `regions`, `force` vector and Mechanical's `pilot` location (the centroid of
  the loaded faces). `supports[]`: the regions fixed by each support entry.
- `exclusion`: the Exclusion Thickness used and the number of excluded faces (M3).
- `mesh.elements`, `optimizer.iterations`, `optimizer.volume_final / volume_initial` (target 0.60; early stops
  just below are accepted), `wall_time.solve_topology` (s), `cores`.
- `static_result[]`: the full-design deflection of each load's faces (`u_along_load_avg`), before optimization.

## rebuild.json

- `status`: `fail` means FIX and LOAD ended up in different bodies (`reason` says which), or an input check failed.
- `volumes`: `opt`, `added` (by the keeps), `trimmed` (outside SPACE), `final`, `islands`.
- `region_body`, `components[]`: which body holds each region. `passive_attached`: PASSIVE regions in the main
  body. A detached PASSIVE region is recorded, not a failure: its material goes to `islands.stl`.

## metrics.json — fidelity first

- `pass.preserved_surface`: **the main result.** True when every attached region's preserved surface P lies
  within `measure.surface_tol` (0.02 mm) of the rebuilt part. Rebuilt runs so far give ≤ 3e-8 mm. For raw runs
  (`M0_*`, `rebuilt: false`) it is `null` (reported only).
- `preserved_surface[]`, per region: `max_deviation`, `p99_deviation`, `beyond_fraction` (share of samples beyond
  the tolerance), `n_samples` (0.1 mm spacing), `status` (`measured` / `detached` / `no_samples`).
- `pass.load_path`: FIX and LOAD are connected.
- `seams[]`, per keep part: the fraction of SPACE's volume that the part keeps in 0.5 mm distance bands outside
  the keep. `flagged` when a valley dips by more than 25 %: a neck where the material meets the keep. A seam flag
  is a warning to look at the part, not a failure.
- `summary`: worst values over regions, `surface_regions_failing`, `detached`, `seam_flags`.

## validation.json — stiffness on the rebuilt part

- `status`: `ok`, or `not_validated` with `reason` (e.g. the wrap failed at every retry size, or the mesh has
  no nodes on a BC region).
- `converged`: the compliance changed by less than 1 % between the last two refinement levels.
  `levels[]` gives the sizes, element counts, BC node counts, `wrap_failures` (retries) and `compliance_change`.
- `pilot_u_along_load` (one per load), `stiffness` (= ΣF²/compliance; F/u for one load), `mass`,
  `specific_stiffness` (N/mm per kg; the secondary metric).
- `strain_energy` vs `mechanical.json` `optimizer.compliance_final` (Mechanical's "compliance" is strain
  energy, ½F·u): the **model-form gap**. A few % or less means the rebuilt part behaves like the optimizer's
  model.
- `stress`: `member_vm_max` (away from the keeps) and per region `surface_vm_max` and `seam_vm_max` (1 mm
  outside the keep). **Indicative only**: not mesh-converged, never pass/fail.

## Comparing runs

`python -m pipeline.collect --part <part>` writes `results/<part>/summary.csv` (one row per run: fidelity,
connectivity, volumes, stiffness, cost, `specific_stiffness_vs_ref` against the finest M3 run) and three plots.
The Phase 6 report `results/general_geometry.md` shows how earlier results were written up.
