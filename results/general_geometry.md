# General geometry: Phase 6 regression and first bracket runs

Runs from 2026-10-20/21, all on Ansys 2026 R1 with 24 cores. The data is in `runs/<part>/<run>/` and
`results/<part>/summary.csv` (regenerate with `python -m pipeline.collect --part <part>`).

## Summary

- **Feature preservation:** every rebuilt run reproduces every preserved surface exactly, on all three parts. The
  max deviation is ≤ 3e-8 mm, and no sample is beyond the 0.02 mm tolerance. Raw M0 on the bracket misses by up
  to 9.8 mm, and by 0.26–0.30 mm on the FIX and LOAD holes.
- **Beam regression:** the generic pipeline reproduces Phase 4 exactly. It matches the same 8 bores, with the same
  element counts, iterations and volumes. Specific stiffness is equal to within 5e-7. The re-marked beam (Preserve
  region keeps) gives results identical to the hand-modelled beam.
- **Bracket:** it runs end to end on off-axis holes, a slot and a counterbore. The full-design BC check agrees with
  Mechanical to 0.03 %. M3 at h = 2 is the setting to use, as on the beam.

## 1. Beam regression (requirements from plan Phase 6)

| Requirement | beam M3_h4 | beam M3_h2 | beam_marked M3_h2 |
|---|---|---|---|
| Face matching | 8 bores, 1 face each | 8 bores, 1 face each | FIX_1 4, LOAD_1 2, PASSIVE_1 2 faces |
| Elements / iterations | 16 860 / 9 (= Phase 4) | 80 242 / 18 (= Phase 4) | 80 242 / 18 (= beam) |
| Optimizer volume, V_opt, V_final | identical to Phase 4 | identical to Phase 4 | identical to beam |
| Specific stiffness (N/mm/kg) | 1 463.022 (Phase 4: 1 463.021) | 1 615.878 (Phase 4: 1 615.878) | 1 615.878 |
| Preserved surface, max deviation | 1.9e-9 mm | 1.1e-9 mm | 1.1e-9 mm |
| Beam-only bore and seat metrics | as Phase 4 (bore error 0.0002 mm, seat 0) | as Phase 4 | as Phase 4 |
| Seam flags (new / old metric) | none / none | none / none | none / none |

The marked beam is identical because the Preserve region feature selects the same 8 bore faces with the same
t = 3.75 mm, so Mechanical runs the same optimization.

The seam metric is now a volume fraction rather than the old absolute cross-section, so the dip values differ.
For example, beam M3_h4's largest dip is 0.12 at LOAD_1 with the new metric and 0.23 with the old. Both stay
under the 0.25 flag on every beam keep.

## 2. Bracket (L-bracket, 150 N in +X on the upright holes)

Regions: FIX_1 is the two base holes (vertical axes), LOAD_1 the two upright holes (horizontal axes), PASSIVE_1 the
slot (4 faces) and PASSIVE_2 the counterbored hole's lower bore and step. Face matching found 2 + 2 + 4 + 2 faces,
the counts the attributes record. The unselected counterbore wall was correctly left out.

| Run | Preserved surface, max (mm) | Worst region beyond 0.02 mm | Connected / PASSIVE attached | Seam flags | Specific stiffness (N/mm/kg) | vs M3_h2 | TO time (s) |
|---|---|---|---|---|---|---|---|
| M3_h4 | 1.7e-9 | 0 % | yes / 2 of 2 | LOAD_1 (both keeps, dips 0.49 and 0.39) | 279.1 | −3.0 % | 137 |
| **M3_h2** | 1.7e-8 | 0 % | yes / 2 of 2 | none | **287.6** | — | 224 |
| M0_h2 raw | 9.8 | 69 % (PASSIVE_1) | yes / 2 of 2 | none | not validated | | 220 |
| M0R_h2 | 3.0e-8 | 0 % | yes / 2 of 2 | PASSIVE_1 slot (dip 0.40) | 289.2 | +0.6 % | 220 |

Raw M0_h2, per region: FIX_1 is off by up to 0.30 mm (39 % of samples beyond tolerance), LOAD_1 by 0.26 mm
(38 %), the PASSIVE_1 slot by 9.8 mm (69 %) and the PASSIVE_2 counterbore by 2.6 mm (54 %). M0 doesn't protect
PASSIVE regions at all, so the slot and counterbore are partly filled in. The rebuild restores all of them exactly.

What the seam flags show:
- **M3_h4, LOAD keeps:** they sit in sparse material. Only 15 % of the first band outside each keep is kept, and
  the fraction dips to 6 % about 3.5 mm out before rising again. At h = 2 the dips are 0.20 and 0.17, below the
  flag.
- **M0R_h2, slot:** the material is thinnest right at the keep boundary (40 % kept, rising to 66 % further out).
  That is the typical neck when the keep is only added after optimization; M3_h2's dip at the slot is 0.21.

Other results:
- **Validation:** all bracket runs converge in two levels, with compliance changing by at most 0.26 %. The
  model-form gap is +1.4 % (M3_h4), +0.7 % (M3_h2) and −0.5 % (M0R_h2).
- **Full-design BC check:** validating SPACE itself with the M3_h2 BCs gives a pilot displacement along the load
  of 7.6037 mm. Mechanical's full-design static averages 7.6059 mm over the loaded faces, a difference of −0.03 %.
  The generic BC chain (face matching → named selections → S-triangle nodes → RBE3 pilot) reproduces Mechanical's
  model.
- **M3 vs M0R at h = 2:** both have the same specific stiffness to within 0.6 %. But M0R adds 2.4 times as much
  keep material (1 903 vs 777 mm³) and necks at the slot.
- **The test load is heavy for a PETG bracket this size:** 9 mm deflection and 54–88 MPa indicative member stress.
  This doesn't affect the comparisons (linear statics), but your own part should use the real load and material.

## 3. Changes made while running (details in plan Phase 6)

- **Face area tolerance 1 %:** Mechanical's `Face.Area` comes from its coarse display tessellation, so a bore reads
  0.16 % low. The face count is the strict check.
- **P needs K's material behind it.** Otherwise the 0.5 mm of counterbore wall next to the marked step's void
  margin counted as preserved, and M0R_h2 failed there by exactly m = 0.5 mm.
- **Validation robustness:**
  - The CDB is now parsed in-house; Smart App Control blocked the VTK DLLs that `ansys.mapdl.reader` loads.
  - A failed Prime wrap is retried at ±10 % feature size. The bracket at 1.5/0.4 mm self-intersects; M3_h4 and
    M0R_h2 level 0 used 0.36 mm.
- **Zero-volume sliver bodies:** the booleans sometimes leave them (bracket M3_h2: 4, M0R_h2: 7). They are now
  dropped and counted.

## 4. For Gate 4

1. **Retire the beam-only metrics?** Recommended. The preserved-surface metric agrees with the bore-fit and seat
   metrics on every beam run: rebuilt runs are exact, and raw M0 fails. It also works on the bracket, where the
   old ones can't.
2. **Settings for your part:** M3 + rebuild at h = 2, then validate. On the bracket, h = 4 loses 3 % specific
   stiffness and necks at the LOAD keeps; h = 2 doesn't. That matches the beam's recommendation.
3. **Your part's YAML:** use the real load and material. The PETG placeholder and the 150 N test load are for
   comparisons only.
