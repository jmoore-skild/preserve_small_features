import functools

import pytest

from pipeline import geom, synth
from pipeline.config import load_config

H = 4.0   # synthetic opt meshes mimic a 4 mm TO mesh


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def space(cfg):
    return geom.to_trimesh(geom.make_space(cfg))


@pytest.fixture(scope="session")
def keep(cfg):
    return geom.to_trimesh(geom.union_all(geom.make_keep_parts(cfg).values()))


@pytest.fixture(scope="session")
def case(cfg):
    """case(base, defects) -> synthetic opt mesh, cached for the session."""
    return functools.lru_cache(maxsize=None)(lambda base, defects="": synth.make_case(cfg, base, defects))
