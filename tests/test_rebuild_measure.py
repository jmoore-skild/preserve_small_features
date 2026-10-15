"""Phase 2 acceptance: each metric flags exactly what was planted (plan Section 7, Phase 2)."""
import numpy as np
import pytest

from pipeline import measure, rebuild
from pipeline.rebuild import RebuildError
from tests.conftest import H

BASES = ["exact", "coarse"]


def run(case, keep, space, cfg, base, defects=""):
    res = rebuild.rebuild(case(base, defects), keep, space, cfg)
    met = measure.measure(res.final, cfg, H, rebuild_report=res.report)
    return res, met


def by_bore(records, name):
    return [r for r in records if r["bore"] == name]


# --------------------------------------------------------------------------- baselines


def test_metrics_pass_on_exact_space(cfg, space):
    """Sanity: the metrics themselves read the true CAD as exact."""
    assert all(r["ok"] for r in measure.bore_fidelity(space, cfg))
    assert all(r["ok"] for r in measure.seat_flatness(space, cfg))


@pytest.mark.parametrize("base", BASES)
def test_clean_rebuild_passes(case, keep, space, cfg, base):
    res, met = run(case, keep, space, cfg, base)
    assert met["pass"] == {"bore_fidelity": True, "seat_flatness": True, "load_path": True}
    assert all(res.report["passive_attached"].values())
    assert res.islands is None
    assert met["summary"]["seam_flags"] == []


def test_coarse_raw_is_defective(case, cfg):
    """The coarse mimic really does distort bores and seats before rebuild (M0-style, report-only)."""
    met = measure.measure(case("coarse"), cfg, H, rebuilt=False)
    assert met["pass"]["bore_fidelity"] is None and met["pass"]["seat_flatness"] is None
    assert not any(r["ok"] for r in met["bores"])
    assert met["summary"]["seat_max_deviation"] > 0.1


# --------------------------------------------------------------------------- (a) undersized faceted bores


@pytest.mark.parametrize("base", BASES)
def test_a_undersized_bores_removed(case, keep, space, cfg, base):
    raw = measure.bore_fidelity(case(base, "a"), cfg)
    assert not any(r["ok"] for r in raw)
    _, met = run(case, keep, space, cfg, base, "a")
    assert met["pass"]["bore_fidelity"] and met["pass"]["seat_flatness"]
    assert met["summary"]["bore_max_abs_radius_error"] < 0.005


# --------------------------------------------------------------------------- (b) bulge past a side face


@pytest.mark.parametrize("base", BASES)
def test_b_bulge_trimmed(case, keep, space, cfg, base):
    T = cfg["beam"]["thickness"]
    raw = measure.seat_flatness(case(base, "b"), cfg)
    fix3_top = [r for r in raw if r["bore"] == "FIX_3" and r["face_y"] == T][0]
    assert fix3_top["max_deviation"] > 0.4

    res, met = run(case, keep, space, cfg, base, "b")
    assert met["pass"]["seat_flatness"] and met["pass"]["bore_fidelity"]
    assert res.final.bounds[1][1] <= T + 1e-6
    clean, _ = run(case, keep, space, cfg, base)
    assert res.report["volumes"]["trimmed"] > clean.report["volumes"]["trimmed"] + 10.0   # chord bulge ≈ 40 mm³


# --------------------------------------------------------------------------- (c) necked seam


@pytest.mark.parametrize("base", BASES)
def test_c_neck_flagged(case, keep, space, cfg, base):
    _, met = run(case, keep, space, cfg, base, "c")
    assert met["summary"]["seam_flags"] == ["FIX_4"]
    assert met["pass"]["load_path"]


# --------------------------------------------------------------------------- (d) PASSIVE island


@pytest.mark.parametrize("base", BASES)
def test_d_passive_island_recorded(case, keep, space, cfg, base):
    res, met = run(case, keep, space, cfg, base, "d")
    assert res.report["status"] == "ok"
    assert res.report["fix_load_connected"]
    assert res.report["passive_attached"] == {"PASSIVE_1": False, "PASSIVE_2": False}
    assert res.islands is not None and res.report["volumes"]["islands"] > 0
    islands = [c for c in res.report["components"] if c["role"] == "island"]
    assert any({"PASSIVE_1", "PASSIVE_2"} <= set(c["probes"]) for c in islands)
    # Detachment is recorded, not a failure; final.stl holds no PASSIVE material.
    assert met["pass"] == {"bore_fidelity": True, "seat_flatness": True, "load_path": True}
    assert met["summary"]["detached"] == ["PASSIVE_1", "PASSIVE_2"]
    probes = rebuild.keep_probes(cfg)
    assert not res.final.contains(np.array([probes["PASSIVE_1"], probes["PASSIVE_2"]])).any()


# --------------------------------------------------------------------------- FIX/LOAD separation


def test_split_is_hard_fail(case, keep, space, cfg):
    with pytest.raises(RebuildError, match="FIX and LOAD are not in one body") as ei:
        rebuild.rebuild(case("exact", "split"), keep, space, cfg)
    assert ei.value.args[1]["status"] == "fail"


# --------------------------------------------------------------------------- (e) and input checks


@pytest.mark.parametrize("base", BASES)
def test_e_non_watertight_fails_loudly(case, keep, space, cfg, base):
    with pytest.raises(RebuildError, match="opt is not a closed.*No automatic repair"):
        rebuild.rebuild(case(base, "e"), keep, space, cfg)


def test_metres_instead_of_mm_rejected(case, keep, space, cfg):
    opt = case("exact").copy()
    opt.apply_scale(1e-3)
    with pytest.raises(RebuildError, match="volume ratio.*unit mismatch"):
        rebuild.rebuild(opt, keep, space, cfg)


def test_frame_mismatch_rejected(case, keep, space, cfg):
    opt = case("exact").copy()
    opt.apply_translation([0.0, 0.0, 40.0])
    with pytest.raises(RebuildError, match="inside space.*frame or unit mismatch"):
        rebuild.rebuild(opt, keep, space, cfg)


# --------------------------------------------------------------------------- combined


@pytest.mark.parametrize("base", BASES)
def test_combined_abcd(case, keep, space, cfg, base):
    res, met = run(case, keep, space, cfg, base, "abcd")
    assert met["pass"] == {"bore_fidelity": True, "seat_flatness": True, "load_path": True}
    assert met["summary"]["seam_flags"] == ["FIX_4"]
    assert res.report["passive_attached"] == {"PASSIVE_1": False, "PASSIVE_2": False}
    assert res.final.bounds[1][1] <= cfg["beam"]["thickness"] + 1e-6


# --------------------------------------------------------------------------- STL round trip


@pytest.mark.parametrize("defects", ["", "abcd", "split"])
def test_outputs_survive_stl_round_trip(case, keep, space, cfg, tmp_path, defects):
    """STL is float32 and unindexed: written meshes must still be closed volumes on reload."""
    from pipeline import geom
    p = tmp_path / "opt.stl"
    case("exact", defects).export(p)
    assert geom.load_mesh(p).is_volume
    if defects != "split":
        res = rebuild.rebuild(geom.load_mesh(p), keep, space, cfg)
        res.final.export(tmp_path / "final.stl")
        assert geom.load_mesh(tmp_path / "final.stl").is_volume
