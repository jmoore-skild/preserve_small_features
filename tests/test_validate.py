"""validate.py's BC node selection and stress bands, on a fake FE mesh (no Ansys)."""
import numpy as np

from pipeline import geom, validate
from pipeline.config import bore_radius, bores, keep_radius


def fake_fe(geo, interior=2000, seed=0):
    """Nodes on SPACE's surface (0.5 mm spacing) plus random interior nodes; no elements needed here."""
    surf, _ = geom.sample_surface(geo.space, 0.5)
    rng = np.random.default_rng(seed)
    lo, hi = geo.space.bounds
    box = lo + rng.random((interior * 3, 3)) * (hi - lo)
    pts = np.vstack([surf, box[geom.inside(geo.space, box)]])
    n = len(pts)
    return validate.FeMesh(None, pts, np.arange(1, n + 1), np.zeros((0, 4), dtype=int), np.ones(n, dtype=bool),
                           np.array([1]))


def test_bc_nodes_lie_on_their_bores(geo, cfg):
    mech = {"supports": [{"regions": ["FIX_1", "FIX_2", "FIX_3", "FIX_4"]}],
            "loads": [{"regions": ["LOAD_1", "LOAD_2"], "force": [0, 0, -200.0], "pilot": [190, 7.5, 30]}]}
    bcs = validate.bc_surfaces(geo, mech, cfg)
    fe = fake_fe(geo)
    tol = cfg["validate"]["bc_surface_tol"]
    r = bore_radius(cfg)
    for b in bores(cfg):
        m = validate.surface_nodes(fe, bcs.s_tris[b.name], tol)
        p = fe.points[m]
        assert len(p) > 100
        rho = np.hypot(p[:, 0] - b.x, p[:, 2] - b.z)
        assert np.all(np.abs(rho - r) <= tol + 1e-6)                    # on this bore only
    # Interior nodes and the far side faces carry nothing.
    all_bc = np.zeros(len(fe.points), dtype=bool)
    for t in bcs.s_tris.values():
        all_bc |= validate.surface_nodes(fe, t, tol)
    assert not all_bc[-1]


def test_stress_bands_use_distance_to_keep(geo, cfg):
    fe = fake_fe(geo)
    vm = np.ones(len(fe.points))
    R = keep_radius(cfg)
    b = next(b for b in bores(cfg) if b.name == "PASSIVE_1")
    rho = np.hypot(fe.points[:, 0] - b.x, fe.points[:, 2] - b.z)
    vm[np.abs(rho - (R + cfg["validate"]["seam_sample_offset"])) < 0.05] = 50.0   # a hot ring at the seam offset
    vm[rho < R] = 99.0                                                              # inside the keep: excluded
    region_nodes = {r.name: np.zeros(len(vm), dtype=bool) for r in geo.regions}
    rep = validate.stress_report(fe, vm, geo, region_nodes, cfg)
    assert rep["regions"]["PASSIVE_1"]["seam_vm_max"] == 50.0
    assert rep["regions"]["PASSIVE_1"]["seam_nodes"] > 0
    assert rep["member_vm_max"] == 1.0
