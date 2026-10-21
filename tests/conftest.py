import functools

import pytest

from pipeline import regions as rg, synth
from pipeline.config import load_config

H = 4.0   # synthetic opt meshes mimic a 4 mm TO mesh


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def geo(cfg):
    """The procedural beam with hand-modelled keeps: regions FIX_1..4, LOAD_1..2, PASSIVE_1..2."""
    return rg.standin_geometry(cfg)


@pytest.fixture(scope="session")
def space(geo):
    return geo.space


@pytest.fixture(scope="session")
def keep(geo):
    return geo.keep


@pytest.fixture(scope="session")
def case(cfg):
    """case(base, defects) -> synthetic opt mesh, cached for the session."""
    return functools.lru_cache(maxsize=None)(lambda base, defects="": synth.make_case(cfg, base, defects))
