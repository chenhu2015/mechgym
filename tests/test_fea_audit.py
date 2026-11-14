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


def annulus(a=1.0, b=2.0, nr=2, nt=2):
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


def with_inner_pressure(calls, inner, p=50.0):
    nodes = {c[1][0]: c[1][1:3] for c in calls if c[0] == "node"}
    return calls + [["load", [n, p * nodes[n][0], p * nodes[n][1]], {}] for n in inner]


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
    ([["load", [1, 0.0, 10.0], {}]], "radial"),                      # node 1 at (1, 0): tangential load
    ([["block2D", [2, 2, 1, 1, "quad", 1.0, "PlaneStrain", 1], {}]], "block2D"),
])
def test_cylinder_exploits_are_rejected(extra, needle):
    calls, inner, _ = annulus()
    problems = audit_opensees(with_inner_pressure(calls, inner) + extra, CYL)
    assert problems and any(needle in p for p in problems), problems


def test_hole_loads_only_on_outer_boundary():
    calls, inner, outer = annulus()
    nodes = {c[1][0]: c[1][1:3] for c in calls if c[0] == "node"}
    ok = calls + [["load", [n, -10.0 * nodes[n][0], 0.0], {}] for n in outer]
    assert audit_opensees(ok, HOLE) == []
    bad = ok + [["load", [inner[0], 1.0, 0.0], {}]]                # on the traction-free wall
    assert any("outer (loaded) boundary" in p for p in audit_opensees(bad, HOLE))


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
