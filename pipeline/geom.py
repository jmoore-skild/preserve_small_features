"""Shared mesh helpers: manifold conversion, procedural stand-in geometry, band sampling, cylinder fits.

Frame (plan Section 4): X along the beam, Y through the thickness (bore axes), Z vertical.
Shapes drawn in the XZ plane are built as 2D cross-sections in local (x, z) and extruded along Y.
"""
from __future__ import annotations

import math

import manifold3d as m3d
import numpy as np
import trimesh
from scipy.optimize import least_squares

from pipeline.config import bore_radius, bores, circular_segments, keep_radius


# --------------------------------------------------------------------------- conversion

def to_manifold(mesh: trimesh.Trimesh) -> m3d.Manifold:
    m = m3d.Manifold(m3d.Mesh64(
        vert_properties=np.array(mesh.vertices, dtype=np.float64, order="C"),   # copy: trimesh arrays may be read-only
        tri_verts=np.array(mesh.faces, dtype=np.uint64, order="C"),
    ))
    if m.status() != m3d.Error.NoError:
        raise ValueError(f"mesh is not an oriented 2-manifold ({m.status()})")
    return m


# Booleans can leave sliver edges shorter than float32 resolution (~1e-5 mm at 200 mm). STL stores
# float32, so on reload those vertices merge and the mesh stops being watertight. Collapse them first.
SLIVER_TOL = 1e-4   # mm


def to_trimesh(m: m3d.Manifold) -> trimesh.Trimesh:
    mesh = m.simplify(SLIVER_TOL).to_mesh64()
    return trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3],
                           faces=np.asarray(mesh.tri_verts, dtype=np.int64), process=False)


def load_mesh(path) -> trimesh.Trimesh:
    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"{path}: not a single triangle mesh")
    return mesh


# --------------------------------------------------------------------------- point queries (libigl)
# trimesh's ray-based contains() and rtree proximity queries take minutes on the 1e5-1e6 points the generic
# metrics need (and contains() runs out of memory); libigl's AABB tree and fast winding number take seconds.

def _vf(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces, dtype=np.int64)


def inside(mesh: trimesh.Trimesh, points: np.ndarray) -> np.ndarray:
    """Points inside a closed mesh (generalized winding number > 1/2). Points on the surface are ambiguous."""
    import igl
    if len(points) == 0:
        return np.zeros(0, dtype=bool)
    return igl.fast_winding_number(*_vf(mesh), np.ascontiguousarray(points, dtype=np.float64)) > 0.5


def distance(mesh: trimesh.Trimesh, points: np.ndarray) -> np.ndarray:
    """Unsigned distance from each point to the mesh surface."""
    import igl
    if len(points) == 0:
        return np.zeros(0)
    d2, _, _ = igl.point_mesh_squared_distance(np.ascontiguousarray(points, dtype=np.float64), *_vf(mesh))
    return np.sqrt(d2)


def signed_distance(mesh: trimesh.Trimesh, points: np.ndarray) -> np.ndarray:
    """Distance to the surface, positive inside (trimesh's convention); the sign comes from the winding number."""
    return np.where(inside(mesh, points), 1.0, -1.0) * distance(mesh, points)


def sample_surface(mesh: trimesh.Trimesh, spacing: float, faces: np.ndarray | None = None
                   ) -> tuple[np.ndarray, np.ndarray]:
    """About one point per spacing² of area on the triangles (all, or the `faces` indices). Returns (points, face
    index of each point). Every triangle gets at least one point.

    Each triangle's points follow a fixed low-discrepancy (R2) pattern in barycentric coordinates, so a triangle
    gets the same points whichever other triangles are sampled, and they move with the mesh under a rigid motion."""
    idx = np.arange(len(mesh.faces)) if faces is None else np.asarray(faces)
    tri = mesh.triangles[idx]
    counts = np.maximum(1, np.ceil(mesh.area_faces[idx] / spacing ** 2).astype(np.int64))
    t = np.repeat(tri, counts, axis=0)
    j = np.arange(len(t)) - np.repeat(np.cumsum(counts) - counts, counts)     # index within its triangle
    g = 1.32471795724474602596                                                # plastic number
    u, v = (0.5 + j[None, :] * np.array([[1 / g], [1 / g ** 2]])) % 1.0
    flip = u + v > 1.0
    u[flip], v[flip] = 1.0 - u[flip], 1.0 - v[flip]
    pts = t[:, 0] + u[:, None] * (t[:, 1] - t[:, 0]) + v[:, None] * (t[:, 2] - t[:, 0])
    return pts, np.repeat(idx, counts)


# --------------------------------------------------------------------------- primitives

def along_y(local: m3d.Manifold, y0: float, y1: float) -> m3d.Manifold:
    """Map a solid built in local (x, z_world, 0..y1-y0) coordinates onto world Y in [y0, y1]."""
    return local.rotate([90.0, 0.0, 0.0]).translate([0.0, y1, 0.0])


def prism_y(cs: m3d.CrossSection, y0: float, y1: float) -> m3d.Manifold:
    """Extrude an XZ-plane cross-section through Y in [y0, y1]."""
    return along_y(m3d.Manifold.extrude(cs, y1 - y0), y0, y1)


def cylinder_y(x: float, z: float, r: float, y0: float, y1: float, segments: int) -> m3d.Manifold:
    return prism_y(m3d.CrossSection.circle(r, segments).translate([x, z]), y0, y1)


def ring_y(x: float, z: float, r_in: float, r_out: float, y0: float, y1: float, segments: int) -> m3d.Manifold:
    cs = m3d.CrossSection.circle(r_out, segments) - m3d.CrossSection.circle(r_in, segments)
    return prism_y(cs.translate([x, z]), y0, y1)


# --------------------------------------------------------------------------- procedural stand-in

def make_space(cfg: dict) -> m3d.Manifold:
    """Box minus the through-bores: the procedural stand-in for Onshape's SPACE part."""
    b = cfg["beam"]
    r = bore_radius(cfg)
    n = circular_segments(cfg, r)
    box = m3d.Manifold.cube([b["length"], b["thickness"], b["height"]])
    holes = [cylinder_y(bo.x, bo.z, r, -1.0, b["thickness"] + 1.0, n) for bo in bores(cfg)]
    return box - m3d.Manifold.batch_boolean(holes, m3d.OpType.Add)


def make_keep_parts(cfg: dict) -> dict[str, m3d.Manifold]:
    """Solid full-thickness keep cylinders, keyed KEEP_<ZONE>_<i>."""
    T = cfg["beam"]["thickness"]
    R = keep_radius(cfg)
    n = circular_segments(cfg, R)
    return {bo.keep_name: cylinder_y(bo.x, bo.z, R, 0.0, T, n) for bo in bores(cfg)}


def union_all(parts) -> m3d.Manifold:
    return m3d.Manifold.batch_boolean(list(parts), m3d.OpType.Add)


# --------------------------------------------------------------------------- band sampling + cylinder fit

def sample_band(mesh: trimesh.Trimesh, cx: float, cz: float, r_in: float, r_out: float,
                y_lo: float, y_hi: float, density: float = 20.0, seed: int = 0) -> np.ndarray:
    """Points sampled on the mesh surface inside an annular band about a Y-parallel axis at (cx, cz).

    STL cylinders carry vertices only at their ends, so the band is sampled on the facets rather
    than taken from vertices. Facet sag at the export tolerance is ~0.0003 mm on a 2.25 mm bore.
    """
    tri = mesh.triangles
    lo, hi = tri.min(axis=1), tri.max(axis=1)
    cand = ((hi[:, 0] >= cx - r_out) & (lo[:, 0] <= cx + r_out)
            & (hi[:, 2] >= cz - r_out) & (lo[:, 2] <= cz + r_out)
            & (hi[:, 1] >= y_lo) & (lo[:, 1] <= y_hi))
    tri = tri[cand]
    if len(tri) == 0:
        return np.empty((0, 3))
    area = trimesh.triangles.area(tri)
    counts = np.minimum(np.ceil(area * density).astype(int), 5000)
    rng = np.random.default_rng(seed)
    t = np.repeat(tri, counts, axis=0)
    u, v = rng.random((2, len(t)))
    flip = u + v > 1.0
    u[flip], v[flip] = 1.0 - u[flip], 1.0 - v[flip]
    pts = t[:, 0] + u[:, None] * (t[:, 1] - t[:, 0]) + v[:, None] * (t[:, 2] - t[:, 0])
    rho = np.hypot(pts[:, 0] - cx, pts[:, 2] - cz)
    keep = (rho >= r_in) & (rho <= r_out) & (pts[:, 1] >= y_lo) & (pts[:, 1] <= y_hi)
    return pts[keep]


def fit_cylinder_y(pts: np.ndarray, cx0: float, cz0: float, r0: float, y_mid: float) -> dict:
    """Least-squares cylinder with a near-Y axis: x = cx + a (y - y_mid), z = cz + b (y - y_mid)."""
    def resid(p):
        cx, cz, a, b, rho = p
        dy = pts[:, 1] - y_mid
        return np.hypot(pts[:, 0] - (cx + a * dy), pts[:, 2] - (cz + b * dy)) - rho

    sol = least_squares(resid, x0=[cx0, cz0, 0.0, 0.0, r0], method="lm")
    cx, cz, a, b, rho = sol.x
    return {"cx": cx, "cz": cz, "tilt_x": a, "tilt_z": b, "radius": rho,
            "rms": float(np.sqrt(np.mean(sol.fun ** 2)))}
