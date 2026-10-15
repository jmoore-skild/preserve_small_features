"""geometry_check passes on the procedural stand-in and catches a misplaced keep part / coarse export."""
from pipeline import geom, geometry_check


def test_standin_passes(tmp_path, cfg):
    geometry_check.write_standin(tmp_path, cfg)
    checks, keep = geometry_check.check_geometry(tmp_path, cfg)
    assert checks.ok, [c for c in checks.items if not c["ok"]]
    assert keep.is_volume and len(keep.split(only_watertight=False)) == 8


def test_offset_keep_fails(tmp_path, cfg):
    geometry_check.write_standin(tmp_path, cfg)
    p = tmp_path / "keep_parts" / "KEEP_LOAD_2.stl"
    m = geom.load_mesh(p)
    m.apply_translation([0.05, 0.0, 0.0])
    m.export(p)
    checks, _ = geometry_check.check_geometry(tmp_path, cfg)
    failed = {c["check"] for c in checks.items if not c["ok"]}
    assert failed == {"KEEP_LOAD_2 coaxial"}


def test_coarse_tessellation_fails(tmp_path, cfg):
    coarse = {**cfg, "onshape_export": {**cfg["onshape_export"], "stl_angle_tolerance": 10.0}}
    geometry_check.write_standin(tmp_path, coarse)          # 36-segment bores
    checks, _ = geometry_check.check_geometry(tmp_path, cfg)  # checked against the real 2° spec
    failed = {c["check"] for c in checks.items if not c["ok"]}
    assert {f"bore {b} tessellation" for b in ("FIX_1", "LOAD_1", "PASSIVE_2")} <= failed
