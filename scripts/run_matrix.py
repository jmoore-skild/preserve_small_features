"""Run TO solves and their post-processing for one part, one step per process, in order.

    python -m scripts.run_matrix --part bracket M3_h4 M0_h2 M3_h2   # run ids from config/runs/
    python -m scripts.run_matrix --part bracket M3_h2 --skip-solve  # post-process an existing TO result

M3/M2 runs: run_mechanical -> rebuild -> measure -> validate.
M0 runs:    run_mechanical -> measure --raw, then M0R_h<h>: copy opt.stl, config.yaml and mechanical.json
            (the TO run's part, h and BCs) -> rebuild -> measure -> validate.

Steps run strictly in sequence: Mechanical, Prime and MAPDL all check out the one `ansys` licence seat the
pool grants. Each step's output goes to runs/<part>/<id>/<step>.log; a failed step stops that run's chain and the
driver moves on to the next run.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time

from pipeline.config import REPO, load_run_config, run_dir as run_path


def step(run_dir, name: str, *module_args: str) -> bool:
    run_dir.mkdir(parents=True, exist_ok=True)
    log = run_dir / f"{name}.log"
    t = time.perf_counter()
    with open(log, "w", encoding="utf-8") as f:
        rc = subprocess.run([sys.executable, "-u", "-m", *module_args], cwd=REPO, stdout=f,
                            stderr=subprocess.STDOUT).returncode
    print(f"{run_dir.parent.name}/{run_dir.name:12s} {name:10s} {'ok' if rc == 0 else f'FAILED ({rc})':12s} "
          f"{time.perf_counter() - t:7.0f} s   {log.relative_to(REPO)}", flush=True)
    return rc == 0


def post(run_dir) -> None:
    d = str(run_dir.relative_to(REPO))
    (step(run_dir, "rebuild", "pipeline.rebuild", "--run-dir", d)
     and step(run_dir, "measure", "pipeline.measure", "--run-dir", d)
     and step(run_dir, "validate", "pipeline.validate", "--run-dir", d))


def run(part: str, run_id: str, solve: bool) -> None:
    rc = load_run_config(part, run_id)["run"]
    run_dir = run_path(part, run_id)
    if solve and not step(run_dir, "mechanical", "pipeline.run_mechanical", "--part", part, "--run", run_id):
        return
    if rc["method"] != "M0":
        post(run_dir)
        return
    step(run_dir, "measure", "pipeline.measure", "--run-dir", str(run_dir.relative_to(REPO)), "--raw")
    m0r = run_path(part, run_id.replace("M0_", "M0R_", 1))
    m0r.mkdir(parents=True, exist_ok=True)
    for name in ("opt.stl", "config.yaml", "mechanical.json"):
        shutil.copy2(run_dir / name, m0r / name)
    post(m0r)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", required=True, help="part name (config/parts/<part>.yaml, geometry/<part>/)")
    ap.add_argument("runs", nargs="+", help="run ids (config/runs/<id>.yaml)")
    ap.add_argument("--skip-solve", action="store_true", help="post-process existing TO results only")
    a = ap.parse_args(argv)
    for run_id in a.runs:
        run(a.part, run_id, solve=not a.skip_solve)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
