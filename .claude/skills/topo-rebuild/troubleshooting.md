# Troubleshooting

Read the failing step's log first: `runs/<part>/<run>/<step>.log`, or the console output for export and check.

## Export (`onshape_export`)

| Symptom | Cause and fix |
|---|---|
| `environment variable 'ONSHAPE_ACCESS_KEY' is not set` | API keys not set. Create them in Onshape (developer portal) and set them as user environment variables `ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY`. |
| HTTP 401/403 | Wrong keys, or no access to the document. |
| HTTP 404 | Wrong IDs: check `onshape.url` in the part YAML (a workspace URL, `/w/`, not a version `/v/`). |
| `no part named SPACE`, `malformed keep name`, `Preserve region attributes on parts that aren't named as keeps (renamed?)` | Rename in Onshape; don't rename keeps made by Preserve region (change the label in the feature instead). |
| Takes ~10 min | Normal: Onshape's STEP translation is sometimes slow. |

## Geometry check (`geometry_check`)

| Failing check | Meaning and fix |
|---|---|
| `space volume vs Onshape` | The STL is incomplete or the wrong part was exported. Re-export. |
| `... watertight volume` | A broken part in Onshape. Look for open or self-intersecting geometry. |
| `at least one FIX and one LOAD region` | Add the missing Preserve region. |
| `part YAML regions exist with the right role` | The YAML names a region that doesn't exist, or one with the wrong role. Automatic labels renumber when an earlier region is deleted; give regions typed labels if the YAML lists them by name. |
| `... straddles its faces` | The keep doesn't reach into both the material and the void. Selected faces may not be on `SPACE`, or `SPACE` lacks the feature (it's cut only into the keep). |
| `... S area vs attribute` | The keep covers more or less of `SPACE`'s surface than the faces selected. Usually the faces are on another part, or `SPACE` changed after marking. |
| `FIX and LOAD keeps don't overlap` | FIX and LOAD keeps touch. Leave material between them, or reduce t. |

## Mechanical (`run_mechanical`)

| Symptom | Cause and fix |
|---|---|
| `legacy Provider loading failed` | OpenSSL clash. Something imported `hashlib` before `preload_ansys_openssl`; keep trimesh imports inside `run()`. |
| `FaceMatchError` | CAD faces don't match the regions (`mechanical.json` `regions` shows counts and areas). Usually a SPACE changed after export, or a keep that covers part of a face that wasn't split. |
| `different wall thicknesses` | Regions use different t. Mechanical takes one Exclusion Thickness per run: make t equal on all Preserve regions. |
| Solve fails at 24 cores, then succeeds at 12 or 4 | HPC packs not granted. Expected; the run records the cores used. |
| Licence errors at every core count | Another Ansys process holds the seat (check `tasklist`), or the licence server is down. |

## Rebuild, measure

| Symptom | Cause and fix |
|---|---|
| `opt is not a closed ... volume` | Bad TO export. Rerun the solve. |
| `volume ratio ... unit mismatch` / `inside space ... frame` | `opt.stl` doesn't match `geometry/<part>` (wrong part, or the geometry was re-exported after the run). |
| `FIX and LOAD are not in one body` | The optimizer cut the load path. Try h = 2 or finer, or check the loads. |
| `pass.preserved_surface` false on a rebuilt run | Should not happen. Check `geometry/<part>` is the geometry the run used, and look at `preserved_surface[]` for the region and its `max_deviation`. |

## Validation (`validate`)

| Symptom | Cause and fix |
|---|---|
| `wrap failed ...; retrying` in the log | Prime's wrapper failed at one size; it retries at ±10 % feature size. Fine if a retry succeeds. |
| `not_validated`, `Prime could not wrap` | All three sizes failed. Try `validate.feature_size` 0.3 or 0.5 in the part YAML. |
| `no surface nodes for a BC` | The wrap lost a BC face. Look for a very thin feature; try a smaller `validate.feature_size`. |
| `An Application Control policy has blocked this file` | Smart App Control blocked a DLL. Validation no longer imports `ansys.mapdl.reader`; if another import triggers it, find which and avoid it. Don't turn Smart App Control off. |
| MAPDL fails to start at every process count | Licence: see Mechanical above. |
| `converged: false` | Three levels without < 1 % change. Report the last level and the trend. |
