# Modelling a part for exclude-and-rebuild topology optimization

This guide is for anyone preparing an Onshape part for the pipeline in this repository. The pipeline optimizes
the part's material layout and then puts the small functional features back exactly: bores, seats, mating pads
and slots. You tell it which features those are by marking them in Onshape.

## What the pipeline needs

One Part Studio per part, containing:

- **One part named `SPACE`.** This is the design space: the largest envelope the optimized part may occupy, with
  every small feature already cut in (holes, counterbores, slots, pads). Anything outside `SPACE` can never be
  material. Anything inside it may be removed unless a keep protects it.
- **Keep parts named `KEEP_<ROLE>_<label>`.** Each one marks a functional interface whose faces must come out
  exactly as modelled. Make them with the **Preserve region** feature (below). They overlap `SPACE`; that's
  intended.

Roles:

| Role | Meaning in the analysis |
|---|---|
| `FIX` | Fixed support on these faces (where the part is bolted down) |
| `LOAD` | The load enters here (a remote force spread over these faces) |
| `PASSIVE` | No boundary condition; the faces are just kept exact (e.g. a mounting hole for something light) |

There must be at least one `FIX` and one `LOAD` region. Loads, supports and material are set per part in
`config/parts/<part>.yaml`, by region name (`FIX_1`, `LOAD_TIP`, ...), not in Onshape.

## The Preserve region feature

Add it from the feature toolbar (Feature Studio `PreserveRegion` in the working document). Parameters:

- **Faces to preserve:** the faces that must stay exact, all on `SPACE`.
- **Role:** FIX, LOAD or PASSIVE.
- **Label:** A–Z, 0–9 and `_` (default `1`). Role + label is the region's name, e.g. `FIX_1`. Use a different
  label for each Preserve region with the same role.
- **Wall thickness t (into the material):** the wall around the faces that must stay solid. This is also the
  Exclusion Thickness the optimizer keeps frozen.
- **Void margin m (into the void):** default 0.5 mm. It makes the keep straddle the faces, so the faces in the
  final part come from `SPACE` itself and stay exact. Keep it smaller than any hole radius it grows into.

The feature thickens the selected faces into a keep solid (shown semi-transparent: blue FIX, red LOAD, grey
PASSIVE). Faces that touch along an edge become one keep; separate groups become `KEEP_<ROLE>_<label>_1`,
`_2`, and so on. One feature can therefore mark a whole bolt pattern.

## Choosing the faces

- **A hole:** select its cylindrical face. For a counterbored hole, select the bore, the counterbore wall and the
  step face together; they form one connected group.
- **Part of a face** (a washer seat, a mating pad): split the face first (Split, or a sketch on the face plus
  Split face), then select only the split region.
- **One Preserve region per functional interface.** All the bolts of one flange can be one region, while a
  bearing seat and a mounting pattern should be separate regions, so their loads can be set separately.
- Don't mark faces that are free to change. Every marked face adds frozen material.

## Choosing t and m

- **t** is the wall that must survive around the feature. For bolted holes, about half the washer or head
  diameter minus the hole radius is a good start. The beam study used t = 3.75 mm (a Ø12 boss around a Ø4.5
  hole) and found that thicker exclusions (8 mm) cost stiffness without improving fidelity.
- **m** only needs to be larger than the export tolerance. 0.5 mm works; it must be smaller than the radius of
  any concave face it grows into, or the feature fails with "Thicken failed".
- Every region on a part should normally use the same t. (Whether Mechanical accepts several exclusion
  thicknesses in one run is still being checked in Phase 6.)

## Mistakes to avoid

- **FIX and LOAD keeps touching or overlapping.** The load would go straight into the support, and the check
  `FIX and LOAD keeps don't overlap` fails. Leave clear material between them.
- **Renaming keep parts** made by the feature. The exporter matches each keep to its feature by name; change the
  label in the feature instead.
- **Selecting faces on a part other than `SPACE`.** The feature rejects faces on more than one part, but it
  can't check the part's name. `geometry_check` catches it: the keep then covers no `SPACE` face, or the wrong
  area of one.
- **Features not cut into `SPACE`.** If a hole is only in a keep, `SPACE` has no face there to preserve.
- **Keeps reaching outside `SPACE`** by more than the void margin. That material can't exist in the final part.

## Hand-modelled keeps

You can model a keep solid yourself and name it `KEEP_<ROLE>_<label>`, as the original beam does. It must
straddle the preserved faces in the same way. Because it carries no feature data, set its wall thickness in the
part YAML (`keeps.wall_thickness`), and the face-area check is skipped for it.

## Then

```
python -m pipeline.onshape_export --part <part>
python -m pipeline.geometry_check --part <part>
```

Fix anything `geometry_check` reports before running the optimization.
