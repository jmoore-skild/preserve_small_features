"""Config loading: base.yaml + optional per-run override, plus derived quantities."""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
BASE_CONFIG = REPO / "config" / "base.yaml"
PARTS_DIR = REPO / "config" / "parts"
RUNS_CONFIG = REPO / "config" / "runs"


@dataclass(frozen=True)
class Bore:
    zone: str        # FIX / LOAD / PASSIVE
    index: int       # 1-based within the zone
    x: float
    z: float

    @property
    def name(self) -> str:
        return f"{self.zone}_{self.index}"

    @property
    def keep_name(self) -> str:
        return f"KEEP_{self.name}"


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(*overrides: str | Path | dict, base: str | Path = BASE_CONFIG) -> dict:
    """Load base.yaml and deep-merge each override (path or dict) in order."""
    cfg = yaml.safe_load(Path(base).read_text(encoding="utf-8"))
    for o in overrides:
        if not isinstance(o, dict):
            o = yaml.safe_load(Path(o).read_text(encoding="utf-8")) or {}
        cfg = _deep_merge(cfg, o)
    return cfg


def bores(cfg: dict) -> list[Bore]:
    out = []
    for zone, spec in cfg["zones"].items():
        for i, (x, z) in enumerate(spec["bores"], start=1):
            out.append(Bore(zone, i, float(x), float(z)))
    return out


def bore_radius(cfg: dict) -> float:
    return cfg["bore"]["diameter"] / 2.0


def keep_radius(cfg: dict) -> float:
    return cfg["keep"]["diameter"] / 2.0


def circular_segments(cfg: dict, radius: float) -> int:
    """Segment count matching the Onshape STL tolerances (chord and angle), rounded up to a multiple of 4."""
    exp = cfg["onshape_export"]
    n_angle = 360.0 / exp["stl_angle_tolerance"]
    n_chord = math.pi / math.acos(max(-1.0, 1.0 - exp["stl_chord_tolerance"] / radius))
    n = math.ceil(max(n_angle, n_chord))
    return int(4 * math.ceil(n / 4))


def load_part(name: str) -> dict:
    """config/parts/<name>.yaml, with `name` filled in and the BC lists defaulted to empty."""
    path = PARTS_DIR / f"{name}.yaml"
    if not path.exists():
        known = sorted(p.stem for p in PARTS_DIR.glob("*.yaml"))
        raise FileNotFoundError(f"no part config {path}; known parts: {known}")
    part = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    part["name"] = name
    part.setdefault("supports", [])
    part.setdefault("loads", [])
    return part


def load_run_config(part: str, run_id: str | None = None) -> dict:
    """base.yaml, then config/parts/<part>.yaml, then config/runs/<run_id>.yaml. `part` holds the part name, and
    `supports` / `loads` the part's BC lists."""
    p = load_part(part)
    cfg = load_config({k: v for k, v in p.items() if k != "name"},
                      *([RUNS_CONFIG / f"{run_id}.yaml"] if run_id else []))
    cfg["part"] = part
    return cfg


def read_run_config(run_dir: Path) -> dict:
    """The resolved config a run saved (runs/<part>/<id>/config.yaml), over the current base.yaml so that settings
    added since the run take their defaults; the run's own values win."""
    path = Path(run_dir) / "config.yaml"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run_mechanical writes it (run_matrix copies it for M0R runs)")
    return load_config(path)


def geometry_dir(part: str) -> Path:
    return REPO / "geometry" / part


def run_dir(part: str, run_id: str) -> Path:
    return REPO / "runs" / part / run_id
