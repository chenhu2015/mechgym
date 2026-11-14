"""Admissibility audit of a recorded OpenSeesPy model: is it the stated problem?

The verifier solves the agent's model itself, so numbers computed outside the model never count.
That alone does not stop a model from *containing* the answer: a closed-form value can be injected
through a prescribed displacement, a pre-strained stiff truss, a calibrated spring, a body force or
a node tie. This audit rejects such models. It allows what a model of the stated problem needs:

- 2D continuum elements (``quad``, ``bbarQuad``, ``enhancedQuad``, ``SSPquad``, ``tri31``) in plane
  strain, with no element pressure or body force;
- the stated material: ``ElasticIsotropic`` with the stated E and nu, or, for the footing,
  ``J2Plasticity`` with the stated stiffness and strength and (nearly) no hardening, optionally
  through a ``PlaneStrain`` wrapper;
- supports (``fix``, and ``sp`` with value 0);
- nodal loads only on the loaded boundary (elastic problems), or prescribed settlements only on
  the footing nodes (footing).

Everything else (uniaxial materials, other elements, ``equalDOF``, ``rigidLink``, ``eleLoad``,
nonzero ``sp`` elsewhere, mesh-generation commands whose elements cannot be checked) is rejected.
"""

from __future__ import annotations

import math
from collections import Counter

import numpy as np

from .sandbox import DEFINE

REL_TOL = 0.005            # material constants must match the statement within 0.5%
GEOM_TOL = 0.005           # a node is "on" a circle of radius R if |r - R| <= 0.5% R
MAX_HARDENING = 1e-3       # footing: hardening modulus at most 1e-3 G ("perfectly plastic")
LOAD_ANGLE_DEG = 2.0       # cylinder: nodal loads must be radial within 2 degrees

# element name -> (number of nodes, index of matTag, index of type string or None,
#                  indices of arguments that must be zero if present)
ELEMENTS = {
    "quad":         (4, 7, 6, (8, 10, 11)),        # tag n1-n4 thick type mat [pressure rho b1 b2]
    "enhancedQuad": (4, 7, 6, ()),                 # tag n1-n4 thick type mat
    "bbarQuad":     (4, 6, None, ()),              # tag n1-n4 thick mat
    "SSPquad":      (4, 5, 6, (8, 9)),             # tag n1-n4 mat type thick [b1 b2]
    "tri31":        (3, 6, 5, (7, 9, 10)),         # tag n1-n3 thick type mat [pressure rho b1 b2]
}
# rho (density) creates no load in a static analysis, so it is not checked.
ALLOWED = {"wipe", "model", "node", "fix", "fixX", "fixY", "nDMaterial", "element", "timeSeries",
           "pattern", "load", "sp", "mass"}


def _close(x: float, ref: float, tol: float = REL_TOL) -> bool:
    return abs(x - ref) <= tol * abs(ref)


def audit_opensees(calls: list, spec: dict, footing_nodes: list[int] | None = None) -> list[str]:
    """Return a list of problems; empty means the model is admissible for ``spec``."""
    problems: list[str] = []
    kind = spec["problem"]
    e, nu = spec.get("youngs_modulus"), spec.get("poisson_ratio")

    used = {name for name, _, _ in calls}
    for name in sorted((used & DEFINE) - ALLOWED):
        problems.append(f"{name} is not allowed in this model")
    nodes: dict[int, tuple[float, float]] = {}
    mats: dict[int, list] = {}
    elems: list[tuple[str, list]] = []
    loads: list[tuple[int, list[float]]] = []
    sps: list[tuple[int, int, float]] = []
    for name, args, _ in calls:
        if name == "model":
            flat = [str(a) for a in args]
            if "-ndm" in flat and flat[flat.index("-ndm") + 1] != "2":
                problems.append("the model must be two-dimensional (-ndm 2)")
        elif name == "node":
            nodes[int(args[0])] = (float(args[1]), float(args[2]))
        elif name == "nDMaterial":
            mats[int(args[1])] = [args[0], *args[2:]]
        elif name == "element":
            elems.append((str(args[0]), list(args[1:])))
        elif name == "load":
            loads.append((int(args[0]), [float(v) for v in args[1:3]]))
        elif name == "sp":
            sps.append((int(args[0]), int(args[1]), float(args[2])))

    # --- materials -----------------------------------------------------------------------------
    def material_ok(tag: int, depth: int = 0) -> str | None:
        m = mats.get(tag)
        if m is None:
            return f"material {tag} is not defined"
        mtype = m[0]
        if mtype in ("PlaneStrain", "PlaneStrainMaterial") and depth == 0:
            return material_ok(int(m[1]), 1)
        if kind == "strip-footing":
            if mtype != "J2Plasticity":
                return f"material {tag} ({mtype}): the clay must be modeled with J2Plasticity"
            k, g, sig0, sig_inf = (float(v) for v in m[1:5])
            delta = float(m[5]) if len(m) > 5 else 0.0
            h = float(m[6]) if len(m) > 6 else 0.0
            k_ref, g_ref = e / (3 * (1 - 2 * nu)), e / (2 * (1 + nu))
            if not (_close(k, k_ref) and _close(g, g_ref)):
                return f"material {tag}: K and G do not match the stated E and nu"
            if not _close(sig0, math.sqrt(3.0) * spec["undrained_strength"]):
                return f"material {tag}: yield stress does not match the stated s_u"
            if abs(h) > MAX_HARDENING * g or (delta != 0.0 and not _close(sig_inf, sig0)):
                return f"material {tag}: the clay must be (nearly) perfectly plastic"
            return None
        if mtype != "ElasticIsotropic":
            return f"material {tag} ({mtype}): the medium must be ElasticIsotropic"
        if not (_close(float(m[1]), e) and _close(float(m[2]), nu)):
            return f"material {tag}: E and nu do not match the statement"
        return None

    connected: set[int] = set()
    edge_count: Counter = Counter()
    for etype, a in elems:
        if etype not in ELEMENTS:
            problems.append(f"element type {etype} is not allowed (continuum elements only)")
            continue
        nn, imat, itype, izero = ELEMENTS[etype]
        en = [int(v) for v in a[1:1 + nn]]
        connected.update(en)
        for i, j in zip(en, en[1:] + en[:1]):
            edge_count[frozenset((i, j))] += 1
        try:
            mtag = int(a[imat])
        except (IndexError, TypeError, ValueError):
            problems.append(f"{etype} element {a[0]}: cannot read its material")
            continue
        if itype is not None and str(a[itype]) != "PlaneStrain":
            problems.append(f"{etype} element {a[0]}: must be PlaneStrain")
        if any(i < len(a) and float(a[i]) != 0.0 for i in izero):
            problems.append(f"{etype} element {a[0]}: element pressure and body forces must be 0")
        msg = material_ok(mtag)
        if msg:
            problems.append(msg)
    if not elems:
        problems.append("the model has no elements")
    boundary = {n for edge, c in edge_count.items() if c == 1 for n in edge}

    # --- prescribed displacements and loads ----------------------------------------------------
    foot = set(footing_nodes or [])
    settlements = set()
    for node, dof, value in sps:
        if value == 0.0:
            continue
        if kind == "strip-footing" and node in foot and dof == 2:
            settlements.add(value)
            continue
        problems.append(f"nonzero prescribed displacement at node {node} (only the footing "
                        "settlement may be nonzero)" if kind == "strip-footing"
                        else f"nonzero prescribed displacement at node {node} (supports must be 0)")
    if len(settlements) > 1:
        problems.append("the footing nodes must all be given the same settlement")

    if kind == "strip-footing":
        if loads:
            problems.append("the footing must be loaded by prescribed settlement only (no nodal loads)")
    elif kind == "thick-cylinder":
        a_in = spec["inner_radius"]
        for node, f in loads:
            if node not in nodes:
                continue
            x, y = nodes[node]
            r = math.hypot(x, y)
            if not _close(r, a_in, GEOM_TOL) or node not in boundary:
                problems.append(f"load at node {node} is not on the inner (pressurized) surface")
                break
            fr = (f[0] * x + f[1] * y) / r
            if math.hypot(*f) > 0 and (fr <= 0 or math.degrees(math.acos(min(1.0, fr / math.hypot(*f))))
                                       > LOAD_ANGLE_DEG):
                problems.append(f"load at node {node} is not an outward radial pressure load")
                break
    elif kind == "circular-hole":
        a_h = spec["hole_radius"]
        for node, f in loads:
            if node not in nodes:
                continue
            r = math.hypot(*nodes[node])
            if node not in boundary or r <= a_h * (1 + GEOM_TOL):
                problems.append(f"load at node {node} is not on the outer (loaded) boundary")
                break

    stray = sorted(n for n, _ in loads if n not in connected) + \
        sorted(n for n, _, v in sps if v != 0.0 and n not in connected)
    if stray:
        problems.append(f"loads or settlements on nodes outside the mesh: {stray[:5]}")
    return list(dict.fromkeys(problems))


def wall_nodes(calls: list, radius: float, connected_only: bool = True) -> list[int]:
    """Tags of mesh nodes on the circle r = radius (used for the full-wall check)."""
    nodes = {int(a[0]): (float(a[1]), float(a[2])) for n, a, _ in calls if n == "node"}
    conn: set[int] = set()
    for n, a, _ in calls:
        if n == "element" and str(a[0]) in ELEMENTS:
            conn.update(int(v) for v in a[2:2 + ELEMENTS[str(a[0])][0]])
    return [t for t, (x, y) in nodes.items()
            if _close(math.hypot(x, y), radius, GEOM_TOL) and (t in conn or not connected_only)]


def wall_error(coords: np.ndarray, disp: np.ndarray, exact_ur: np.ndarray) -> float:
    """Relative L2 error of the radial displacement along a wall: ||u_fe - u_exact|| / ||u_exact||."""
    r = np.hypot(coords[:, 0], coords[:, 1])
    ur = (disp[:, 0] * coords[:, 0] + disp[:, 1] * coords[:, 1]) / r
    return float(np.linalg.norm(ur - exact_ur) / max(np.linalg.norm(exact_ur), 1e-300))
