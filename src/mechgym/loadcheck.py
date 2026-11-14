"""Static equivalence of the applied loads to the stated boundary traction (all T0 solvers).

The admissibility audits check *where* loads act; this module checks *what* they are. Red-team
agents found two ways to get the closed-form answer out of a load that sits in the right place:

- applying the exact Kirsch traction (instead of the stated uniform far-field stress) on a small
  outer boundary, which reproduces the infinite-plate solution without modeling a far field;
- scaling the load by a calibration factor that cancels the discretization error of a coarse mesh.

Both change the work the applied loads do on smooth displacement fields. For the stated problem the
traction on the loaded boundary is ``t = sigma . n`` with a constant ``sigma`` (``-p I`` for a
pressurized wall, the far-field stress for an opening). For test fields ``v`` that are linear or
quadratic in ``x, y`` the verifier compares the virtual work of the applied loads,
``sum_i f_i . v(x_i)`` (nodal forces) or ``sum_f F_f . v(c_f)`` (face forces), with
``integral of (sigma n) . v ds`` over the loaded boundary of the agent's own mesh. Consistent or
lumped nodal loads of the stated traction match the linear moments to well under 1% on any
reasonable mesh, so the tolerances can be tight.
"""

from __future__ import annotations

import numpy as np

LIN_TOL = 0.005     # resultant and first moments: relative error at most 0.5%
QUAD_TOL = 0.03     # second moments: 3% (lumping error is larger here on coarse meshes)

_GAUSS = (np.array([-np.sqrt(0.6), 0.0, np.sqrt(0.6)]) / 2 + 0.5, np.array([5.0, 8.0, 5.0]) / 18)


def _fields(xy: np.ndarray, scale: float) -> np.ndarray:
    """Test-field values, shape (n, 12, 2): six linear-group fields, then six quadratic ones."""
    x, y = xy[:, 0] / scale, xy[:, 1] / scale
    one, zero = np.ones_like(x), np.zeros_like(x)
    lin = [one, x, y]
    quad = [x * x, x * y, y * y]
    out = []
    for group in (lin, quad):
        for p in group:
            out.append(np.stack([p, zero], axis=1))
            out.append(np.stack([zero, p], axis=1))
    return np.stack(out, axis=1)


def expected_from_segments(segments: list[tuple[np.ndarray, np.ndarray, np.ndarray]],
                           sigma: np.ndarray, scale: float) -> np.ndarray:
    """Virtual work of ``t = sigma n`` on straight boundary segments (start, end, outward normal)."""
    total = np.zeros(12)
    s, w = _GAUSS
    for a, b, n in segments:
        length = float(np.hypot(*(b - a)))
        if length == 0.0:
            continue
        t = sigma @ (n / np.linalg.norm(n))
        pts = a[None, :] + s[:, None] * (b - a)[None, :]
        v = _fields(pts, scale)                          # (3, 12, 2)
        total += length * np.einsum("q,qkc,c->k", w, v, t)
    return total


def applied_moments(points: np.ndarray, forces: np.ndarray, scale: float) -> np.ndarray:
    """Virtual work of point forces on the test fields."""
    if len(points) == 0:
        return np.zeros(12)
    return np.einsum("ikc,ic->k", _fields(np.asarray(points, float), scale), np.asarray(forces, float))


def compare(applied: np.ndarray, expected: np.ndarray) -> dict:
    """Relative errors of the linear-group and quadratic-group moments."""
    lin_ref = max(float(np.linalg.norm(expected[:6])), 1e-300)
    all_ref = max(float(np.linalg.norm(expected)), 1e-300)
    lin = float(np.linalg.norm(applied[:6] - expected[:6]) / lin_ref)
    quad = float(np.linalg.norm(applied[6:] - expected[6:]) / all_ref)
    return {"linear_error": lin, "quadratic_error": quad, "ok": lin <= LIN_TOL and quad <= QUAD_TOL}


def message(kind: str, result: dict) -> str:
    what = "the internal pressure" if kind == "thick-cylinder" else "the stated far-field stress"
    return (f"the applied loads are not statically equivalent to {what} on the loaded boundary "
            f"(resultant/first-moment error {result['linear_error']:.1%}, "
            f"second-moment error {result['quadratic_error']:.1%})")


def stated_stress(spec: dict) -> np.ndarray:
    """The constant stress whose traction ``sigma n`` the loaded boundary must carry."""
    if spec["problem"] == "thick-cylinder":
        return -spec["internal_pressure"] * np.eye(2)
    return np.diag([spec["far_field_stress_x"], spec["far_field_stress_y"]])


def loaded_segments(coords: dict, faces: list[tuple], spec: dict, geom_tol: float) -> list:
    """Straight segments (start, end, outward normal) of the boundary that carries the load.

    ``faces`` holds boundary faces of the agent's mesh as ``(node_i, node_j, mid_or_None,
    cell_centroid)``. Cylinder: faces on the inner wall. Opening: faces that are on neither the
    traction-free wall nor a symmetry axis (x = 0 or y = 0).
    """
    kind = spec["problem"]
    xs = np.array([coords[n] for f in faces for n in f[:2]]) if faces else np.zeros((0, 2))
    size = float(np.abs(xs).max()) if len(xs) else 1.0
    radius = spec["inner_radius"] if kind == "thick-cylinder" else spec["hole_radius"]

    def on_wall(p):
        return abs(np.hypot(*p) - radius) <= geom_tol * radius

    segs = []
    for i, j, mid, centroid in faces:
        a, b = np.asarray(coords[i], float), np.asarray(coords[j], float)
        if kind == "thick-cylinder":
            if not (on_wall(a) and on_wall(b)):
                continue
        else:
            if on_wall(a) and on_wall(b):
                continue
            if (abs(a[0]) <= 1e-6 * size and abs(b[0]) <= 1e-6 * size) or \
                    (abs(a[1]) <= 1e-6 * size and abs(b[1]) <= 1e-6 * size):
                continue
        pts = [a] + ([np.asarray(coords[mid], float)] if mid is not None else []) + [b]
        m = 0.5 * (a + b)
        for p, q in zip(pts, pts[1:]):
            n = np.array([q[1] - p[1], p[0] - q[0]])
            if n @ (m - np.asarray(centroid, float)) < 0:
                n = -n
            segs.append((p, q, n))
    return segs


def boundary_faces(cells: list[list[int]], corners: list[int]) -> list[tuple]:
    """Boundary faces ``(i, j, mid_or_None, centroid_nodes)`` of a 2D mesh.

    ``cells[k]`` lists corner nodes first (counter-clockwise or clockwise) followed, for quadratic
    cells, by one midside node per edge in edge order (MED/Gmsh/OpenSees convention).
    Returns faces with the owning cell's node list in place of the centroid; callers convert it.
    """
    count: dict[frozenset, list] = {}
    for cell, k in zip(cells, corners):
        c = list(cell[:k])
        quadratic = len(cell) >= 2 * k
        for e, (i, j) in enumerate(zip(c, c[1:] + c[:1])):
            key = frozenset((i, j))
            mid = cell[k + e] if quadratic else None
            count.setdefault(key, []).append((i, j, mid, c))
    return [v[0] for v in count.values() if len(v) == 1]


def check_nodal(coords: dict, cells: list[list[int]], corners: list[int], loads: dict, spec: dict,
                geom_tol: float) -> dict:
    """Full check for a nodal-force model (OpenSees loads, Code_Aster assembled loads)."""
    faces = []
    for i, j, mid, cell in boundary_faces(cells, corners):
        centroid = np.mean([coords[n] for n in cell], axis=0)
        faces.append((i, j, mid, centroid))
    segs = loaded_segments(coords, faces, spec, geom_tol)
    if not segs:
        return {"linear_error": float("inf"), "quadratic_error": float("inf"), "ok": False,
                "n_segments": 0}
    scale = max(float(np.abs(np.array([p for s in segs for p in s[:2]])).max()), 1e-300)
    expected = expected_from_segments(segs, stated_stress(spec), scale)
    nodes = [n for n in loads if n in coords]
    applied = applied_moments(np.array([coords[n] for n in nodes]).reshape(-1, 2),
                              np.array([loads[n] for n in nodes]).reshape(-1, 2), scale)
    return {**compare(applied, expected), "n_segments": len(segs)}
