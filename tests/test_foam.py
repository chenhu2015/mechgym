"""T0 OpenFOAM family: case checks, oracle scores 1, and bad or dishonest submissions do not.

The case-check tests run anywhere. The end-to-end tests need OpenFOAM and run inside
docker/foam-dev.Dockerfile.
"""

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from mechgym.foam.audit import audit_case
from mechgym.foam.case import write_kirsch_case
from mechgym.foam.problems import check_case, make_foam_hole, run_case, verify_t0_foam
from mechgym.foam.reader import patch_vectors

HAVE_FOAM = Path("/usr/lib/openfoam/openfoam2412/etc/bashrc").exists()
needs_foam = pytest.mark.skipif(not HAVE_FOAM, reason="OpenFOAM not installed")


@pytest.fixture(scope="module")
def task():
    return make_foam_hole(1)


def write(app: Path, task, **kw) -> Path:
    s = task.spec
    a = s["hole_radius"]
    args = dict(a=a, L=80.0 * a, sx=s["far_field_stress_x"], sy=s["far_field_stress_y"],
                e=s["youngs_modulus"], nu=s["poisson_ratio"], n_ring=40, n_quarter=60, n_far=90,
                far_grading=40.0, end_time=6000)
    args.update(kw)
    write_kirsch_case(app / "case", **args)
    return app / "case"


# --- case checks (no OpenFOAM needed) ----------------------------------------------------------


def test_clean_case_passes(tmp_path, task):
    assert check_case(write(tmp_path, task)) == []


@pytest.mark.parametrize("rel, text, needle", [
    ("0/D", '\nx #codeStream { code #{ system("id"); #}; };\n', "#"),
    ("system/fvSolution", '\n#include "/tests/task.json"\n', "#"),
    ("system/controlDict", "\nfunctions { cheat { type coded; } }\n", "functions"),
    ("system/controlDict", '\nlibs ("libcheat.so");\n', "libs"),
    ("constant/mechanicalProperties", "\nE { type coded; code \"x\"; }\n", "code"),
])
def test_unsafe_dictionaries_rejected(tmp_path, task, rel, text, needle):
    case = write(tmp_path, task)
    (case / rel).write_text((case / rel).read_text() + text)
    problems = check_case(case)
    assert problems and any(needle in p for p in problems), problems


def test_comments_are_ignored(tmp_path, task):
    case = write(tmp_path, task)
    (case / "0/D").write_text((case / "0/D").read_text() + "\n// #include and functions in a comment\n")
    assert check_case(case) == []


def test_missing_file_rejected(tmp_path, task):
    case = write(tmp_path, task)
    (case / "system/fvSchemes").unlink()
    assert any("missing system/fvSchemes" in p for p in check_case(case))


def test_reader_uniform_and_nonuniform(tmp_path):
    f = tmp_path / "D"
    f.write_text("""FoamFile { class volVectorField; }
boundaryField
{
    hole { type fixedValue; value nonuniform List<vector> 2((1 2 3) (4 5 6)); }
    up   { type fixedValue; value uniform (7 8 9); }
}
""")
    assert patch_vectors(f, "hole").tolist() == [[1, 2, 3], [4, 5, 6]]
    assert patch_vectors(f, "up", n_expected=2).tolist() == [[7, 8, 9], [7, 8, 9]]


# --- end to end (OpenFOAM required) ------------------------------------------------------------


def _answer(app: Path, task) -> dict:
    """Run the case once to get the honest claims."""
    res = run_case(app / "case")
    assert res["status"] == "ok", res
    c, d = res["C"][:, :2], res["D"][:, :2]
    th = np.degrees(np.arctan2(c[:, 1], c[:, 0]))
    ur = np.einsum("ij,ij->i", d, c) / np.hypot(c[:, 0], c[:, 1])
    return {"quantities": {"u_r_theta0": float(ur[np.argmin(np.abs(th))]),
                           "u_r_theta90": float(ur[np.argmin(np.abs(th - 90))])}}


@pytest.fixture(scope="module")
def good(tmp_path_factory, task):
    app = tmp_path_factory.mktemp("good")
    write(app, task)
    return app, _answer(app, task)


@needs_foam
def test_converged_case_scores_one(good, task):
    app, ans = good
    r = verify_t0_foam(task.spec, task.meta, ans, app)
    assert r["reward"] == pytest.approx(1.0), r
    assert all(q["rel_error"] < 0.005 for q in r["quantities"].values())


@needs_foam
def test_inflated_claim_halves_score(good, task):
    app, ans = good
    bad = {"quantities": {k: 1.2 * v for k, v in ans["quantities"].items()}}
    r = verify_t0_foam(task.spec, task.meta, bad, app)
    assert r["reward"] == pytest.approx(0.5)


@needs_foam
def test_plane_stress_fails(tmp_path, task):
    write(tmp_path, task, plane_stress=True)
    ans = _answer(tmp_path, task)
    r = verify_t0_foam(task.spec, task.meta, ans, tmp_path)
    assert r["reward"] < 0.5, r


@needs_foam
def test_small_domain_is_penalized(tmp_path, task):
    """A plate only 5 radii wide is far from the infinite medium of the closed-form solution."""
    write(tmp_path, task, L=5.0 * task.spec["hole_radius"], far_grading=4.0)
    ans = _answer(tmp_path, task)
    r = verify_t0_foam(task.spec, task.meta, ans, tmp_path)
    assert r["reward"] < 0.5, r


@needs_foam
def test_coarse_hole_mesh_has_no_face_at_points(tmp_path, task):
    write(tmp_path, task, n_quarter=10, n_ring=10, n_far=30)   # faces 4.5 deg apart: centres at 2.25 deg
    ans = _answer(tmp_path, task)
    r = verify_t0_foam(task.spec, task.meta, ans, tmp_path)
    assert any("no 'hole' face" in f for f in r["flags"]) and r["reward"] == 0.0


@needs_foam
def test_renamed_patch_scores_zero(tmp_path, task):
    case = write(tmp_path, task, end_time=10)
    for rel in ("system/blockMeshDict", "0/D", "0/T"):
        text = (case / rel).read_text()
        (case / rel).write_text(text.replace("hole", "opening"))
    r = verify_t0_foam(task.spec, task.meta, {"quantities": {"u_r_theta0": 0.0, "u_r_theta90": 0.0}}, tmp_path)
    assert r["reward"] == 0.0 and any("hole" in f for f in r["flags"])


# --- admissibility audit (no OpenFOAM needed): the case must be the stated problem ------------


def test_audit_accepts_clean_case(tmp_path, task):
    assert audit_case(write(tmp_path, task), task.spec) == []


def _replace_patch(case, patch, new_body):
    text = (case / "0/D").read_text()
    i = text.index(patch, text.index("boundaryField"))
    j = text.index("}", i)
    (case / "0/D").write_text(text[:i] + f"{patch} {{ {new_body} " + text[j:])


@pytest.mark.parametrize("edit, needle", [
    # red-team exploit: the closed-form wall displacement imposed as a Dirichlet condition
    (lambda c: _replace_patch(c, "hole", "type fixedValue; value nonuniform List<vector> 2((1 0 0) (0 1 0));"),
     "traction-free"),
    (lambda c: _replace_patch(c, "hole", "type tractionDisplacement; traction uniform (5 0 0); "
                                         "pressure uniform 0; value uniform (0 0 0);"), "traction-free"),
    # seeding the solution as the initial field
    (lambda c: (c / "0/D").write_text((c / "0/D").read_text().replace(
        "internalField uniform (0 0 0);", "internalField nonuniform List<vector> 1((1 2 0));")), "internalField"),
    (lambda c: _replace_patch(c, "right", "type fixedValue; value uniform (0.001 0 0);"), "fixedValue"),
    (lambda c: _replace_patch(c, "right", "type fixedGradient; gradient uniform (1 0 0);"), "not allowed"),
    (lambda c: (c / "constant/mechanicalProperties").write_text(
        (c / "constant/mechanicalProperties").read_text().replace("value 0.", "value 0.4", 1)), "E and nu"),
    (lambda c: (c / "constant/thermalProperties").write_text(
        (c / "constant/thermalProperties").read_text().replace("thermalStress no", "thermalStress yes")),
     "thermalStress"),
])
def test_audit_rejects_injected_answers(tmp_path, task, edit, needle):
    case = write(tmp_path, task)
    edit(case)
    problems = audit_case(case, task.spec)
    assert problems and any(needle in p for p in problems), problems
