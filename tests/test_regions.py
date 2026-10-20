"""Preserve-region naming, grouping and surface-in-keep measurement (plan Phase 5)."""
import math

import pytest

from pipeline import geom, regions as rg
from pipeline.config import circular_segments, load_config


def att(role, label, group=1, count=1, t=3.75, m=0.5, faces=1, area=10.0):
    name = f"KEEP_{role}_{label}" + (f"_{group}" if count > 1 else "")
    return name, {"kind": "PreserveRegion", "name": name, "role": role, "label": label, "group": group,
                  "groupCount": count, "wallThicknessMm": t, "voidMarginMm": m, "faceCount": faces,
                  "areaMm2": area}


def test_parse_keep_name():
    assert rg.parse_keep_name("KEEP_FIX_1") == ("FIX", "1")
    assert rg.parse_keep_name("KEEP_LOAD_A1_2") == ("LOAD", "A1_2")
    for bad in ("KEEP_fix_1", "KEEP_FIX_", "KEEP_BOLT_1", "KEEP_FIX_a-1", "SPACE"):
        assert rg.parse_keep_name(bad) is None


def test_groups_from_one_feature_form_one_region():
    attrs = dict(att("FIX", "1", g, 3, area=5.0) for g in (1, 2, 3))
    attrs.update([att("LOAD", "TIP", area=7.0)])
    regs = {r.name: r for r in rg.build_regions({n: "id" for n in attrs}, attrs, None)}
    assert set(regs) == {"FIX_1", "LOAD_TIP"}
    assert len(regs["FIX_1"].keeps) == 3 and regs["FIX_1"].area_mm2 == 15.0 and regs["FIX_1"].face_count == 3
    assert regs["LOAD_TIP"].void_margin == 0.5 and regs["LOAD_TIP"].source == "attribute"


def test_hand_modelled_keeps_take_wall_from_yaml():
    keeps = {f"KEEP_FIX_{i}": "id" for i in (1, 2)}
    regs = rg.build_regions(keeps, {}, 3.75)
    assert [(r.name, r.wall_thickness, r.void_margin, r.area_mm2) for r in regs] == [
        ("FIX_1", 3.75, None, None), ("FIX_2", 3.75, None, None)]
    with pytest.raises(ValueError, match="wall_thickness"):
        rg.build_regions(keeps, {}, None)


@pytest.mark.parametrize("case", ["renamed", "missing_group", "mixed", "malformed"])
def test_inconsistent_keeps_are_rejected(case):
    attrs = dict(att("FIX", "1", g, 2) for g in (1, 2))
    keeps = {n: "id" for n in attrs}
    if case == "renamed":     # the part was renamed after the feature named it
        keeps = {"KEEP_FIX_1_1": "id", "KEEP_FIX_X": "id"}
        attrs = {"KEEP_FIX_1_1": attrs["KEEP_FIX_1_1"], "KEEP_FIX_X": attrs["KEEP_FIX_1_2"]}
    elif case == "missing_group":
        del keeps["KEEP_FIX_1_2"], attrs["KEEP_FIX_1_2"]
    elif case == "mixed":     # a second feature with the same role and label
        n, a = att("FIX", "1", t=2.0)
        keeps[n], attrs[n] = "id", a
    else:
        keeps["KEEP_BOLT_1"] = "id"
    with pytest.raises(ValueError):
        rg.build_regions(keeps, attrs, 3.75)


def test_surface_in_keep_on_beam():
    """Solid keep cylinders (hand-modelled) and straddling tubes (Preserve region) both give S = the bore."""
    cfg = load_config()
    space = geom.to_trimesh(geom.make_space(cfg))
    r, T = cfg["bore"]["diameter"] / 2, cfg["beam"]["thickness"]
    exact = 2 * math.pi * r * T
    solid = geom.to_trimesh(geom.make_keep_parts(cfg)["KEEP_FIX_1"])
    x, z = cfg["zones"]["FIX"]["bores"][0]
    n = circular_segments(cfg, r)
    tube = geom.to_trimesh(geom.ring_y(x, z, r - 0.5, r + 3.75, 0.0, T, n))
    for keep in (solid, tube):
        s = rg.surface_in_keep(space, keep, 0.01)
        assert s == pytest.approx(exact, rel=1e-3)
    # A keep that only touches SPACE's outer face from outside covers none of it.
    outside = geom.to_trimesh(geom.cylinder_y(x, z, 5.0, -2.0, 0.0, n))
    assert rg.surface_in_keep(space, outside, 0.01) == 0.0
