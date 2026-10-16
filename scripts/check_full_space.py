"""BC-equivalence check: validate.py's chain on the full SPACE body vs Mechanical's full-design static.

    python -m scripts.check_full_space        # from the repo root; writes runs/check_full_space/validation.json
"""
import json
import tempfile
from pathlib import Path

from pipeline import geom
from pipeline.config import load_config
from pipeline.validate import launch, remesh_space, validate


def main() -> None:
    # Guarded: validate.tet_mesh spawns child processes, which re-import this module.
    cfg = load_config()
    space = geom.load_mesh("geometry/space.stl")
    work = Path(tempfile.mkdtemp(prefix="validate_"))
    mapdl, nproc = launch(cfg, work)
    try:
        rep = validate(lambda g, b: remesh_space(space, cfg, g, b), space, cfg, mapdl, work)
    finally:
        mapdl.exit()
    mech = json.load(open("runs/M3_h4/mechanical.json"))["static_result"]
    rep["nproc"] = nproc
    rep["mechanical_full_design_uz_load_bores_avg"] = mech["uz_load_bores_avg"]
    Path("runs/check_full_space").mkdir(parents=True, exist_ok=True)
    Path("runs/check_full_space/validation.json").write_text(json.dumps(rep, indent=2))
    print("validate pilot UZ", rep["pilot_uz"], "| Mechanical LOAD-bore UZ avg", mech["uz_load_bores_avg"],
          "| diff %.2f%%" % (100 * (rep["pilot_uz"] / mech["uz_load_bores_avg"] - 1)))


if __name__ == "__main__":
    main()
