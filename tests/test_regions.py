"""Preserve-region naming, grouping and surface-in-keep measurement (plan Phase 5)."""
import math

import numpy as np
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


def _cylinder_points(x, z, r, y0, y1, n=200, seed=0):
    rng = np.random.default_rng(seed)
    a, y = rng.random(n) * 2 * math.pi, y0 + rng.random(n) * (y1 - y0)
    pts = np.column_stack([x + r * np.cos(a), y, z + r * np.sin(a)])
    edges = np.column_stack([x + r * np.cos(a[:20]), np.where(np.arange(20) % 2, y0, y1), z + r * np.sin(a[:20])])
    return np.vstack([pts, edges])         # mesh nodes include the face's boundary edges


def test_match_faces_on_beam():
    """Each bore matches its own region; a side face (flush with the keeps' ends) and the top face match none."""
    cfg = load_config()
    geo = rg.standin_geometry(cfg)
    r, T = cfg["bore"]["diameter"] / 2, cfg["beam"]["thickness"]
    L, H = cfg["beam"]["length"], cfg["beam"]["height"]
    from pipeline.config import bores
    faces = {i: _cylinder_points(b.x, b.z, r, 0.0, T, seed=i) for i, b in enumerate(bores(cfg), start=1)}
    rng = np.random.default_rng(1)
    faces[100] = np.column_stack([rng.random(2000) * L, np.zeros(2000), rng.random(2000) * H])     # y = 0 face
    faces[101] = np.column_stack([rng.random(500) * L, rng.random(500) * T, np.full(500, H)])      # top face
    m = rg.match_faces(faces, geo, 0.01)
    assert m == {b.name: [i] for i, b in enumerate(bores(cfg), start=1)}
    area = {i: 2 * math.pi * r * T for i in faces}
    assert all(rec["ok"] for rec in rg.check_matches(m, area, geo, 1e-3))


def test_match_faces_rejects_a_face_in_two_regions():
    cfg = load_config()
    geo = rg.standin_geometry(cfg)
    parts = dict(geo.keep_parts)
    parts["KEEP_PASSIVE_9"] = parts["KEEP_FIX_1"]          # a second region on the FIX_1 bore
    regions = rg.build_regions({n: "" for n in parts}, {}, 3.75)
    geo2 = rg.PartGeometry(geo.space, regions, parts, geo.keep)
    x, z = cfg["zones"]["FIX"]["bores"][0]
    with pytest.raises(ValueError, match="matches both"):
        rg.match_faces({7: _cylinder_points(x, z, 2.25, 0.0, 15.0)}, geo2, 0.01)


def test_check_matches_against_attribute():
    cfg = load_config()
    geo = rg.standin_geometry(cfg)
    geo.regions[0].keeps[0].face_count, geo.regions[0].keeps[0].area_mm2 = 1, 100.0
    recs = {r["region"]: r for r in rg.check_matches({r.name: [1] for r in geo.regions}, {1: 100.05}, geo, 1e-3)}
    assert recs[geo.regions[0].name]["ok"]
    recs = {r["region"]: r for r in rg.check_matches({r.name: [1] for r in geo.regions}, {1: 100.2}, geo, 1e-3)}
    assert not recs[geo.regions[0].name]["ok"]


def test_preserved_surface_needs_keep_material_behind_it():
    """A counterbore marked on its step: P is the step annulus plus the bore wall the keep backs (depth t), but not
    the counterbore wall beside the void margin."""
    import manifold3d as m3d
    n = 128
    block = m3d.Manifold.cube([30.0, 30.0, 10.0]).translate([-15.0, -15.0, 0.0])
    bore = m3d.Manifold.cylinder(12.0, 2.5, 2.5, n).translate([0.0, 0.0, -1.0])
    cbore = m3d.Manifold.cylinder(6.0, 5.0, 5.0, n).translate([0.0, 0.0, 6.0])     # step at z = 6
    space = geom.to_trimesh(block - bore - cbore)
    # Preserve region on the step: t = 3 into the material (down), m = 0.5 into the void (up).
    ring = m3d.Manifold.cylinder(3.5, 5.0, 5.0, n) - m3d.Manifold.cylinder(5.0, 2.5, 2.5, n).translate([0, 0, -1])
    keep = geom.to_trimesh(ring.translate([0.0, 0.0, 3.0]))
    pts = rg.preserved_samples(space, keep, 0.01, 0.1)
    assert pts[:, 2].max() <= 6.0 + 1e-6                                        # no counterbore wall
    step = np.abs(pts[:, 2] - 6.0) < 1e-6
    wall = np.abs(np.hypot(pts[:, 0], pts[:, 1]) - 2.5) < 0.01
    assert np.all(step | wall)
    assert pts[wall, 2].min() == pytest.approx(3.0, abs=0.05)
    assert step.sum() * 0.01 == pytest.approx(math.pi * (5.0 ** 2 - 2.5 ** 2), rel=0.05)
    # ~18 points per long wall triangle, half of them in the band: an area estimate, hence the looser tolerance
    assert (wall & ~step).sum() * 0.01 == pytest.approx(2 * math.pi * 2.5 * 3.0, rel=0.1)
