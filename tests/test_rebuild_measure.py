"""Phase 2 acceptance on the generic metrics (plan Phase 6): each metric flags exactly what was planted."""
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from pipeline import geom, measure, rebuild, regions as rg
from pipeline.rebuild import RebuildError
from tests.conftest import H

BASES = ["exact", "coarse"]
CLEAN = {"preserved_surface": True, "load_path": True}


def run(case, geo, cfg, base, defects="", legacy=False):
    res = rebuild.rebuild(case(base, defects), geo, cfg)
    met = measure.measure(res.final, geo, cfg, H, rebuild_report=res.report, legacy=legacy)
    return res, met


def surface(met, region):
    return next(r for r in met["preserved_surface"] if r["region"] == region)


# --------------------------------------------------------------------------- baselines


def test_metrics_pass_on_exact_space(cfg, geo):
    """Sanity: the metrics themselves read the true CAD as exact."""
    assert all(r["ok"] for r in measure.preserved_surface(geo.space, geo, cfg))
    assert all(r["ok"] for r in measure.bore_fidelity(geo.space, cfg))
    assert all(r["ok"] for r in measure.seat_flatness(geo.space, cfg))


@pytest.mark.parametrize("base", BASES)
def test_clean_rebuild_passes(case, geo, cfg, base):
    res, met = run(case, geo, cfg, base, legacy=True)
    assert met["pass"] == {**CLEAN, "bore_fidelity": True, "seat_flatness": True}
    assert met["summary"]["surface_max_deviation"] < 0.005
    assert all(res.report["passive_attached"].values())
    assert res.islands is None
    assert met["summary"]["seam_flags"] == []


def test_coarse_raw_is_defective(case, geo, cfg):
    """The coarse mimic really does distort the preserved surfaces before rebuild (M0-style, report-only)."""
    met = measure.measure(case("coarse"), geo, cfg, H, rebuilt=False)
    assert met["pass"]["preserved_surface"] is None
    assert all(r["max_deviation"] > 0.1 for r in met["preserved_surface"])


# --------------------------------------------------------------------------- (a) undersized faceted bores


@pytest.mark.parametrize("base", BASES)
def test_a_undersized_bores_removed(case, geo, cfg, base):
    raw = measure.preserved_surface(case(base, "a"), geo, cfg)
    assert not any(r["ok"] for r in raw)
    _, met = run(case, geo, cfg, base, "a")
    assert met["pass"] == CLEAN
    assert met["summary"]["surface_max_deviation"] < 0.005


# --------------------------------------------------------------------------- (b) bulge past a side face


@pytest.mark.parametrize("base", BASES)
def test_b_bulge_trimmed(case, geo, cfg, base):
    T = cfg["beam"]["thickness"]
    raw = {r["region"]: r for r in measure.preserved_surface(case(base, "b"), geo, cfg)}
    assert raw["FIX_3"]["max_deviation"] > 0.4

    res, met = run(case, geo, cfg, base, "b")
    assert met["pass"] == CLEAN
    assert res.final.bounds[1][1] <= T + 1e-6
    clean, _ = run(case, geo, cfg, base)
    assert res.report["volumes"]["trimmed"] > clean.report["volumes"]["trimmed"] + 10.0   # chord bulge ≈ 40 mm³


# --------------------------------------------------------------------------- (c) necked seam


@pytest.mark.parametrize("base", BASES)
def test_c_neck_flagged(case, geo, cfg, base):
    _, met = run(case, geo, cfg, base, "c")
    assert met["summary"]["seam_flags"] == ["KEEP_FIX_4"]
    assert met["pass"]["load_path"]


# --------------------------------------------------------------------------- (d) PASSIVE island


@pytest.mark.parametrize("base", BASES)
def test_d_passive_island_recorded(case, geo, cfg, base):
    res, met = run(case, geo, cfg, base, "d")
    assert res.report["status"] == "ok"
    assert res.report["fix_load_connected"]
    assert res.report["passive_attached"] == {"PASSIVE_1": False, "PASSIVE_2": False}
    assert res.islands is not None and res.report["volumes"]["islands"] > 0
    islands = [c for c in res.report["components"] if c["role"] == "island"]
    assert any({"PASSIVE_1", "PASSIVE_2"} <= set(c["regions"]) for c in islands)
    # Detachment is recorded, not a failure; final.stl holds no PASSIVE material.
    assert met["pass"] == CLEAN
    assert met["summary"]["detached"] == ["PASSIVE_1", "PASSIVE_2"]
    assert surface(met, "PASSIVE_1")["status"] == "detached"
    for n in ("PASSIVE_1", "PASSIVE_2"):
        core = geom.to_manifold(geo.region_keep(n)) ^ geom.to_manifold(geo.space)
        assert (geom.to_manifold(res.final) ^ core).volume() == pytest.approx(0.0, abs=1e-6)


# --------------------------------------------------------------------------- FIX/LOAD separation


def test_split_is_hard_fail(case, geo, cfg):
    with pytest.raises(RebuildError, match="FIX and LOAD are not in one body") as ei:
        rebuild.rebuild(case("exact", "split"), geo, cfg)
    assert ei.value.args[1]["status"] == "fail"


# --------------------------------------------------------------------------- (e) and input checks


@pytest.mark.parametrize("base", BASES)
def test_e_non_watertight_fails_loudly(case, geo, cfg, base):
    with pytest.raises(RebuildError, match="opt is not a closed.*No automatic repair"):
        rebuild.rebuild(case(base, "e"), geo, cfg)


def test_metres_instead_of_mm_rejected(case, geo, cfg):
    opt = case("exact").copy()
    opt.apply_scale(1e-3)
    with pytest.raises(RebuildError, match="volume ratio.*unit mismatch"):
        rebuild.rebuild(opt, geo, cfg)


def test_frame_mismatch_rejected(case, geo, cfg):
    opt = case("exact").copy()
    opt.apply_translation([0.0, 0.0, 40.0])
    with pytest.raises(RebuildError, match="inside space.*frame or unit mismatch"):
        rebuild.rebuild(opt, geo, cfg)


# --------------------------------------------------------------------------- combined


@pytest.mark.parametrize("base", BASES)
def test_combined_abcd(case, geo, cfg, base):
    res, met = run(case, geo, cfg, base, "abcd")
    assert met["pass"] == CLEAN
    assert met["summary"]["seam_flags"] == ["KEEP_FIX_4"]
    assert res.report["passive_attached"] == {"PASSIVE_1": False, "PASSIVE_2": False}
    assert res.final.bounds[1][1] <= cfg["beam"]["thickness"] + 1e-6


# --------------------------------------------------------------------------- orientation


def test_metrics_independent_of_orientation(case, geo, cfg):
    """Rotate SPACE, the keeps and opt together: every generic metric stays the same within its tolerance."""
    T = np.eye(4)
    T[:3, :3] = Rotation.from_euler("zyx", [37.0, -61.0, 23.0], degrees=True).as_matrix()
    T[:3, 3] = [13.0, -7.0, 42.0]
    geo_r = rg.transformed(geo, T)
    opt = case("coarse", "abcd")
    opt_r = opt.copy().apply_transform(T)

    res, met = run(lambda *_: opt, geo, cfg, "coarse")
    res_r = rebuild.rebuild(opt_r, geo_r, cfg)
    met_r = measure.measure(res_r.final, geo_r, cfg, H, rebuild_report=res_r.report)

    for k in ("final", "added", "trimmed", "islands"):
        assert res_r.report["volumes"][k] == pytest.approx(res.report["volumes"][k], rel=1e-6, abs=1e-3)
    assert res_r.report["region_body"] == res.report["region_body"]
    assert met_r["pass"] == met["pass"] == CLEAN
    assert met_r["summary"]["seam_flags"] == met["summary"]["seam_flags"] == ["KEEP_FIX_4"]
    tol = cfg["measure"]["surface_tol"]
    for a, b in zip(met["preserved_surface"], met_r["preserved_surface"]):
        assert (a["region"], a["status"], a["ok"]) == (b["region"], b["status"], b["ok"])
        if a["max_deviation"] is not None:
            assert b["max_deviation"] == pytest.approx(a["max_deviation"], abs=tol / 10)
    for a, b in zip(met["seams"], met_r["seams"]):
        assert b["max_offset"] == pytest.approx(a["max_offset"], abs=1e-3)
        assert b["max_dip"] == pytest.approx(a["max_dip"], abs=0.05)
    # Raw (report-only) surface deviations rotate with the samples.
    raw = measure.preserved_surface(opt, geo, cfg)
    raw_r = measure.preserved_surface(opt_r, geo_r, cfg)
    for a, b in zip(raw, raw_r):
        assert b["max_deviation"] == pytest.approx(a["max_deviation"], abs=1e-4)


# --------------------------------------------------------------------------- STL round trip


@pytest.mark.parametrize("defects", ["", "abcd", "split"])
def test_outputs_survive_stl_round_trip(case, geo, cfg, tmp_path, defects):
    """STL is float32 and unindexed: written meshes must still be closed volumes on reload."""
    p = tmp_path / "opt.stl"
    case("exact", defects).export(p)
    assert geom.load_mesh(p).is_volume
    if defects != "split":
        res = rebuild.rebuild(geom.load_mesh(p), geo, cfg)
        res.final.export(tmp_path / "final.stl")
        assert geom.load_mesh(tmp_path / "final.stl").is_volume
