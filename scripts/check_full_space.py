"""BC-equivalence check: validate.py's chain on the full SPACE body vs Mechanical's full-design static.

    python -m scripts.check_full_space        # from the repo root; writes runs/check_full_space/validation.json
"""
import json
import shutil
import tempfile
from pathlib import Path

from pipeline import geom
from pipeline.config import load_config
from pipeline.validate import validate


def main() -> None:
    cfg = load_config()
    stl = Path("geometry/space.stl")
    work = Path(tempfile.mkdtemp(prefix="validate_"))
    try:
        rep = validate(stl, geom.load_mesh(stl), cfg, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    mech = json.load(open("runs/M3_h4/mechanical.json"))["static_result"]
    rep["mechanical_full_design_uz_load_bores_avg"] = mech["uz_load_bores_avg"]
    Path("runs/check_full_space").mkdir(parents=True, exist_ok=True)
    Path("runs/check_full_space/validation.json").write_text(json.dumps(rep, indent=2))
    print("validate pilot UZ", rep["pilot_uz"], "| Mechanical LOAD-bore UZ avg", mech["uz_load_bores_avg"],
          "| diff %.2f%%" % (100 * (rep["pilot_uz"] / mech["uz_load_bores_avg"] - 1)))


if __name__ == "__main__":
    main()
