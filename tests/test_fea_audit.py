"""Admissibility audit of recorded OpenSeesPy models (no OpenSeesPy needed: it works on call lists).

Each exploit case reproduces, in miniature, an answer-injection route found by a red-team agent or a
close variant of one.
"""

import math

import pytest

from mechgym.fea.audit import audit_opensees, wall_nodes

CYL = {"problem": "thick-cylinder", "inner_radius": 1.0, "outer_radius": 2.0, "internal_pressure": 100.0,
       "youngs_modulus": 1.0e6, "poisson_ratio": 0.3}
HOLE = {"problem": "circular-hole", "hole_radius": 1.0, "far_field_stress_x": -100.0,
        "far_field_stress_y": -50.0, "youngs_modulus": 1.0e6, "poisson_ratio": 0.3}
FOOT = {"problem": "strip-footing", "footing_width": 2.0, "undrained_strength": 50.0,
        "youngs_modulus": 2.0e4, "poisson_ratio": 0.49}


def annulus(a=1.0, b=2.0, nr=2, nt=8):
    """Quarter annulus, nodes 1..; returns (calls, inner node tags, outer node tags)."""
    calls = [["wipe", [], {}], ["model", ["basic", "-ndm", 2, "-ndf", 2], {}]]
    tag = lambda i, j: 1 + j * (nr + 1) + i  # noqa: E731
    for j in range(nt + 1):
        th = 0.5 * math.pi * j / nt
        for i in range(nr + 1):
            r = a + (b - a) * i / nr
            calls.append(["node", [tag(i, j), r * math.cos(th), r * math.sin(th)], {}])
    calls.append(["nDMaterial", ["ElasticIsotropic", 1, 1.0e6, 0.3], {}])
    e = 0
    for j in range(nt):
        for i in range(nr):
            e += 1
            calls.append(["element", ["quad", e, tag(i, j), tag(i + 1, j), tag(i + 1, j + 1), tag(i, j + 1),
                                      1.0, "PlaneStrain", 1], {}])
    for i in range(nr + 1):
        calls.append(["fix", [tag(i, 0), 0, 1], {}])
        calls.append(["fix", [tag(i, nt), 1, 0], {}])
    calls += [["timeSeries", ["Linear", 1], {}], ["pattern", ["Plain", 1, 1], {}]]
    inner = [tag(0, j) for j in range(nt + 1)]
    outer = [tag(nr, j) for j in range(nt + 1)]
    return calls, inner, outer


def consistent_loads(calls, chain, sigma, inward=False):
    """Consistent nodal loads of the traction sigma.n on the polyline through ``chain``.

    The normal points away from the origin (outer boundary) or towards it (``inward``: the
    pressurized inner wall, whose outward normal for the body points to the centre)."""
    nodes = {c[1][0]: c[1][1:3] for c in calls if c[0] == "node"}
    f = {n: [0.0, 0.0] for n in chain}
    for i, j in zip(chain, chain[1:]):
        (x1, y1), (x2, y2) = nodes[i], nodes[j]
        n = [y2 - y1, x1 - x2]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        if (n[0] * mx + n[1] * my > 0) == inward:
            n = [-n[0], -n[1]]
        t = [sigma[0][0] * n[0] + sigma[0][1] * n[1], sigma[1][0] * n[0] + sigma[1][1] * n[1]]
        for k in (i, j):
            f[k][0] += t[0] / 2
            f[k][1] += t[1] / 2
    return [["load", [n, *f[n]], {}] for n in chain]


def with_inner_pressure(calls, inner, p=100.0, factor=1.0):
    return calls + consistent_loads(calls, inner, [[-p * factor, 0], [0, -p * factor]], inward=True)


def test_clean_cylinder_model_is_admissible():
    calls, inner, _ = annulus()
    assert audit_opensees(with_inner_pressure(calls, inner), CYL) == []


def test_wall_nodes_finds_the_surface():
    calls, inner, outer = annulus()
    assert sorted(wall_nodes(calls, 1.0)) == sorted(inner)
    assert sorted(wall_nodes(calls, 2.0)) == sorted(outer)


@pytest.mark.parametrize("extra, needle", [
    # red-team exploit: pre-strained stiff truss from the graded node to a fixed anchor
    ([["node", [100, 2.0, 0.0], {}], ["fix", [100, 1, 1], {}],
      ["uniaxialMaterial", ["Elastic", 9, 1e14], {}], ["uniaxialMaterial", ["InitStrainMaterial", 10, 9, 1e-3], {}],
      ["element", ["Truss", 99, 1, 100, 1.0, 10], {}]], "Truss"),
    ([["sp", [1, 1, 4.07e-4], {}]], "nonzero prescribed displacement"),
    ([["eleLoad", ["-ele", 1, "-type", "-beamUniform", 1.0], {}]], "eleLoad"),
    ([["equalDOF", [1, 2, 1], {}]], "equalDOF"),
    ([["nDMaterial", ["ElasticIsotropic", 2, 2.0e6, 0.3], {}],
      ["element", ["quad", 50, 1, 2, 5, 4, 1.0, "PlaneStrain", 2], {}]], "E and nu"),
    ([["element", ["quad", 51, 1, 2, 5, 4, 1.0, "PlaneStrain", 1, 0.0, 0.0, 5.0, 0.0], {}]], "body forces"),
    ([["element", ["quad", 52, 1, 2, 5, 4, 1.0, "PlaneStress", 1], {}]], "PlaneStrain"),
    ([["load", [5, 10.0, 0.0], {}]], "not on the inner"),           # node 5 is interior
    ([["load", [1, 0.0, 10.0], {}]], "statically equivalent"),       # node 1 at (1, 0): tangential load
    ([["block2D", [2, 2, 1, 1, "quad", 1.0, "PlaneStrain", 1], {}]], "block2D"),
    # red-team exploit: element thickness as a calibration knob
    ([["element", ["quad", 53, 1, 2, 5, 4, 1.03, "PlaneStrain", 1], {}]], "thickness"),
    ([["timeSeries", ["Linear", 2, "-factor", 0.986], {}]], "time series"),
    ([["pattern", ["Plain", 2, 1, "-fact", 0.986], {}]], "load patterns"),
])
def test_cylinder_exploits_are_rejected(extra, needle):
    calls, inner, _ = annulus()
    problems = audit_opensees(with_inner_pressure(calls, inner) + extra, CYL)
    assert problems and any(needle in p for p in problems), problems


def test_unit_factors_are_accepted():
    calls, inner, _ = annulus()
    calls = [["timeSeries", ["Linear", 1, "-factor", 1.0], {}] if c[0] == "timeSeries" else c for c in calls]
    assert audit_opensees(with_inner_pressure(calls, inner), CYL) == []


def test_calibrated_pressure_is_rejected():
    """Red-team exploit: inner-wall loads scaled by 0.986 to cancel a coarse mesh's error."""
    calls, inner, _ = annulus()
    problems = audit_opensees(with_inner_pressure(calls, inner, factor=0.986), CYL)
    assert any("statically equivalent" in p for p in problems), problems


def test_arc_lumped_pressure_on_a_reasonable_mesh_is_accepted():
    """Honest lumping p * a * dtheta along the radius (not the consistent chord loads) passes."""
    calls, inner, _ = annulus(nr=4, nt=16)
    nodes = {c[1][0]: c[1][1:3] for c in calls if c[0] == "node"}
    dth = 0.5 * math.pi / 16
    loads = []
    for k, n in enumerate(inner):
        w = dth / 2 if k in (0, len(inner) - 1) else dth
        x, y = nodes[n]
        loads.append(["load", [n, 100.0 * w * x, 100.0 * w * y], {}])
    assert audit_opensees(calls + loads, CYL) == []


HOLE_SIGMA = [[-100.0, 0.0], [0.0, -50.0]]


def test_hole_loads_only_on_outer_boundary():
    calls, inner, outer = annulus()
    ok = calls + consistent_loads(calls, outer, HOLE_SIGMA)
    assert audit_opensees(ok, HOLE) == []
    bad = ok + [["load", [inner[0], 1.0, 0.0], {}]]                # on the traction-free wall
    assert any("outer (loaded) boundary" in p for p in audit_opensees(bad, HOLE))


def kirsch_traction(r, th, a=1.0, sx=-100.0, sy=-50.0):
    """Exact Kirsch stress at (r, th) for biaxial far-field stress, as a Cartesian tensor."""
    p, q = (sx + sy) / 2, (sx - sy) / 2
    a2, a4 = a * a / r / r, (a * a / r / r) ** 2
    srr = p * (1 - a2) + q * (1 - 4 * a2 + 3 * a4) * math.cos(2 * th)
    stt = p * (1 + a2) - q * (1 + 3 * a4) * math.cos(2 * th)
    srt = -q * (1 + 2 * a2 - 3 * a4) * math.sin(2 * th)
    c, s = math.cos(th), math.sin(th)
    sxx = srr * c * c + stt * s * s - 2 * srt * s * c
    syy = srr * s * s + stt * c * c + 2 * srt * s * c
    sxy = (srr - stt) * s * c + srt * (c * c - s * s)
    return [[sxx, sxy], [sxy, syy]]


@pytest.mark.parametrize("b", [2.0, 5.0, 10.0])
def test_kirsch_traction_on_small_domain_is_rejected(b):
    """Red-team exploit (all three solvers): exact Kirsch traction on the outer boundary instead of
    the stated uniform far-field stress reproduces the infinite-plate answer on a small domain."""
    calls, _, outer = annulus(a=1.0, b=b, nr=4, nt=16)
    nodes = {c[1][0]: c[1][1:3] for c in calls if c[0] == "node"}
    f = {n: [0.0, 0.0] for n in outer}
    for i, j in zip(outer, outer[1:]):
        (x1, y1), (x2, y2) = nodes[i], nodes[j]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        sig = kirsch_traction(math.hypot(mx, my), math.atan2(my, mx))
        n = [y2 - y1, x1 - x2]
        if n[0] * mx + n[1] * my < 0:
            n = [-n[0], -n[1]]
        for k in (i, j):
            f[k][0] += (sig[0][0] * n[0] + sig[0][1] * n[1]) / 2
            f[k][1] += (sig[1][0] * n[0] + sig[1][1] * n[1]) / 2
    problems = audit_opensees(calls + [["load", [n, *f[n]], {}] for n in outer], HOLE)
    assert any("statically equivalent" in p for p in problems), problems


def test_square_outer_boundary_with_uniform_traction_is_accepted():
    """A quarter plate whose outer boundary is two straight edges (x = L, y = L)."""
    L, nl = 4.0, 4
    calls = [["wipe", [], {}], ["model", ["basic", "-ndm", 2, "-ndf", 2], {}],
             ["nDMaterial", ["ElasticIsotropic", 1, 1.0e6, 0.3], {}]]
    # structured quarter mesh between the hole (r = 1) and the square, along rays to the boundary
    nt, tag = 2 * nl, {}
    for j in range(nt + 1):
        th = 0.5 * math.pi * j / nt
        c, s = math.cos(th), math.sin(th)
        rb = L / max(c, s)
        for i in range(nl + 1):
            r = 1.0 + (rb - 1.0) * i / nl
            tag[i, j] = len(tag) + 1
            calls.append(["node", [tag[i, j], r * c, r * s], {}])
    e = 0
    for j in range(nt):
        for i in range(nl):
            e += 1
            calls.append(["element", ["quad", e, tag[i, j], tag[i + 1, j], tag[i + 1, j + 1], tag[i, j + 1],
                                      1.0, "PlaneStrain", 1], {}])
    calls += [["timeSeries", ["Linear", 1], {}], ["pattern", ["Plain", 1, 1], {}]]
    outer = [tag[nl, j] for j in range(nt + 1)]
    assert audit_opensees(calls + consistent_loads(calls, outer, HOLE_SIGMA), HOLE) == []


def footing_model(material=("J2Plasticity", 1, 2.0e4 / (3 * 0.02), 2.0e4 / 2.98, math.sqrt(3) * 50.0,
                            math.sqrt(3) * 50.0, 0.0, 0.0)):
    calls, _, _ = annulus()   # geometry is irrelevant to the material and loading rules
    calls = [c for c in calls if c[0] != "nDMaterial"]
    calls.insert(2, ["nDMaterial", list(material), {}])
    return calls + [["sp", [1, 2, -0.05], {}], ["sp", [2, 2, -0.05], {}]]


def test_clean_footing_model_is_admissible():
    assert audit_opensees(footing_model(), FOOT, footing_nodes=[1, 2]) == []


@pytest.mark.parametrize("calls, needle", [
    # red-team exploit: elastic soil (plus springs) instead of the plastic clay
    (footing_model(("ElasticIsotropic", 1, 2.0e4, 0.49)), "J2Plasticity"),
    (footing_model(("J2Plasticity", 1, 2.0e4 / 0.06, 2.0e4 / 2.98, 200.0, 200.0, 0.0, 0.0)), "s_u"),
    (footing_model(("J2Plasticity", 1, 2.0e4 / 0.06, 2.0e4 / 2.98, math.sqrt(3) * 50, math.sqrt(3) * 50,
                    0.0, 500.0)), "perfectly plastic"),
    (footing_model() + [["load", [3, 0.0, -10.0], {}]], "settlement only"),
    (footing_model() + [["sp", [3, 2, -0.05], {}]], "only the footing settlement"),
    (footing_model() + [["sp", [1, 2, -0.07], {}]], "same settlement"),
])
def test_footing_exploits_are_rejected(calls, needle):
    problems = audit_opensees(calls, FOOT, footing_nodes=[1, 2])
    assert problems and any(needle in p for p in problems), problems
