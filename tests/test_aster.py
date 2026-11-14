"""T0 Code_Aster family: deck whitelist, oracle scores 1, and bad or dishonest submissions do not.

The whitelist tests run anywhere. The end-to-end tests need Code_Aster and run inside
docker/aster-dev.Dockerfile, which also provides a writable /app (the paths the tasks use).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mechgym.aster.audit import audit_deck, check_load_groups
from mechgym.aster.deck import check_deck
from mechgym.aster.problems import make_aster_cylinder, make_aster_hole, verify_t0_aster

GOOD = """DEBUT()
DEFI_FICHIER(ACTION="ASSOCIER", UNITE=20, FICHIER="/app/mesh.mail", TYPE="ASCII")
mesh = LIRE_MAILLAGE(FORMAT="ASTER", UNITE=20)
model = AFFE_MODELE(MAILLAGE=mesh, AFFE=_F(TOUT="OUI", PHENOMENE="MECANIQUE", MODELISATION="D_PLAN"))
mat = DEFI_MATERIAU(ELAS=_F(E=1.0e6, NU=0.3))
fmat = AFFE_MATERIAU(MAILLAGE=mesh, AFFE=_F(TOUT="OUI", MATER=mat))
groups = ["YSYM", "XSYM"]
load = AFFE_CHAR_MECA(MODELE=model, DDL_IMPO=[_F(GROUP_NO=g, **{}) for g in []] + [_F(GROUP_NO="YSYM", DY=0.0)],
                      PRES_REP=_F(GROUP_MA="INNER", PRES=2 * 50.0))
resu = MECA_STATIQUE(MODELE=model, CHAM_MATER=fmat, EXCIT=_F(CHARGE=load))
FIN()
"""


def test_whitelist_accepts_plain_deck():
    deck = GOOD.replace(", **{}", "")
    assert check_deck(deck) == []


@pytest.mark.parametrize("snippet, reason", [
    ("import os\n", "Import"),
    ("x = resu.getField('DEPL', 1)\n", "Attribute"),
    ("f = CREA_CHAMP(TYPE_CHAM='NOEU_DEPL_R')\n", "CREA_CHAMP"),
    ("resu = CREA_RESU(OPERATION='AFFE')\n", "resu must be assigned only"),
    ("x = open('/tests/task.json')\n", "open"),
    ("x = __import__('os')\n", "__import__"),
    ("DEFI_FICHIER(UNITE=21, FICHIER='/tests/task.json')\n", "FICHIER"),
    ("DEFI_FICHIER(UNITE=21, FICHIER='/app/../tests/task.json')\n", "FICHIER"),
    ("g = FORMULE(VALE=\"__import__('os').system('x')\", NOM_PARA='X')\n", "FORMULE"),
    ("def f():\n    return 1\n", "FunctionDef"),
    ("x = (lambda: 1)()\n", "Lambda"),
    ("x = [c for c in ().__class__.__mro__]\n", "Attribute"),
    ("resu = MODI_REPERE(RESULTAT=resu, reuse=resu)\n", "MODI_REPERE"),
])
def test_whitelist_rejects(snippet, reason):
    problems = check_deck(GOOD.replace(", **{}", "") + snippet)
    assert problems and any(reason in p for p in problems), problems


def test_whitelist_accepts_calc_champ_in_place():
    """The usual post-processing idiom adds stress fields to resu and leaves DEPL alone."""
    deck = GOOD.replace(", **{}", "").replace(
        "FIN()", "resu = CALC_CHAMP(reuse=resu, RESULTAT=resu, CONTRAINTE=('SIGM_ELNO',))\nFIN()")
    assert check_deck(deck) == []


@pytest.mark.parametrize("snippet", [
    "resu = CALC_CHAMP(RESULTAT=resu, CONTRAINTE=('SIGM_ELNO',))\n",          # new result, not in place
    "other = MECA_STATIQUE(MODELE=model, CHAM_MATER=fmat, EXCIT=_F(CHARGE=load))\n"
    "resu = CALC_CHAMP(reuse=resu, RESULTAT=other, CONTRAINTE=('SIGM_ELNO',))\n",
])
def test_whitelist_rejects_calc_champ_replacing_resu(snippet):
    problems = check_deck(GOOD.replace(", **{}", "") + snippet)
    assert any("resu must be assigned only" in p for p in problems), problems


def test_whitelist_calc_champ_alone_is_not_a_result():
    deck = "DEBUT()\nresu = CALC_CHAMP(reuse=resu, RESULTAT=resu)\nFIN()\n"
    assert any("no variable named resu" in p for p in check_deck(deck))


# --- admissibility audit: the deck must be the stated problem ------------------------------------

AUDIT_SPEC = {"problem": "thick-cylinder", "youngs_modulus": 1.0e6, "poisson_ratio": 0.3,
              "inner_radius": 1.0, "outer_radius": 3.0}
CLEAN = GOOD.replace(", **{}", "")


def test_audit_accepts_clean_deck():
    problems, groups = audit_deck(CLEAN, AUDIT_SPEC)
    assert problems == [] and groups == ["INNER"]


def test_audit_accepts_function_tractions():
    deck = CLEAN.replace("FIN()", 'fx = FORMULE(NOM_PARA=("X", "Y"), VALE="-5.0*X")\n'
                         'trac = AFFE_CHAR_MECA_F(MODELE=model, FORCE_CONTOUR=_F(GROUP_MA="OUTER", FX=fx))\nFIN()')
    problems, groups = audit_deck(deck, AUDIT_SPEC)
    assert problems == [] and groups == ["INNER", "OUTER"]


@pytest.mark.parametrize("old, new, needle", [
    # red-team exploit: the closed-form displacement imposed at the graded node
    ('DY=0.0)', 'DY=0.0), _F(GROUP_NO="INNERPT", DX=4.07e-4)', "imposed DX must be 0"),
    ('MODELISATION="D_PLAN"', 'MODELISATION="DIS_T"', "D_PLAN"),
    ("E=1.0e6", "E=1.5e6", "E and NU"),
    ("E=1.0e6", "E=e_val", "numeric constants"),
    ("ELAS=_F(E=1.0e6, NU=0.3)", "ELAS=_F(E=1.0e6, NU=0.3), THER=_F(LAMBDA=1.0)", "only define ELAS"),
    ('PRES_REP=_F(GROUP_MA="INNER"', 'FORCE_NODALE=_F(GROUP_NO="INNERPT", FX=1.0), PRES_REP=_F(GROUP_MA="INNER"',
     "FORCE_NODALE"),
    ('PRES_REP=_F(GROUP_MA="INNER"', 'PRES_REP=_F(TOUT="OUI"', "GROUP_MA"),
    ('PRES_REP=_F(GROUP_MA="INNER"', 'PRES_REP=_F(GROUP_MA=groups[0]', "string literals"),
    ("FIN()", "c = AFFE_CARA_ELEM(MODELE=model)\nFIN()", "AFFE_CARA_ELEM"),
    ("FIN()", 'f = AFFE_CHAR_MECA_F(MODELE=model, DDL_IMPO=_F(GROUP_NO="INNERPT", DX=fx))\nFIN()', "DDL_IMPO"),
])
def test_audit_rejects_injected_answers(old, new, needle):
    deck = CLEAN.replace(old, new)
    assert deck != CLEAN
    problems, _ = audit_deck(deck, AUDIT_SPEC)
    assert problems and any(needle in p for p in problems), problems


def test_load_groups_must_be_on_the_loaded_boundary():
    # 2 x 2 quads on 1 <= x <= 2, 0 <= y <= 1; node 4 at (1.5, 0.5) is interior, node 0 on r = a
    res = {"coords": [[x, y] for y in (0.0, 0.5, 1.0) for x in (1.0, 1.5, 2.0)],
           "cells": [[0, 1, 4, 3], [1, 2, 5, 4], [3, 4, 7, 6], [4, 5, 8, 7]], "cell_types": [204] * 4,
           "load_groups": {"INSIDE": [4]}}
    spec = {"problem": "circular-hole", "hole_radius": 1.0}
    assert any("inside the mesh" in p for p in check_load_groups(spec, res))
    res["load_groups"] = {"WALL": [0]}
    assert any("traction-free" in p for p in check_load_groups(spec, res))
    res["load_groups"] = {"EDGE": [2, 5, 8]}
    assert check_load_groups(spec, res) == []


def test_whitelist_requires_resu():
    assert any("no variable named resu" in p for p in check_deck("DEBUT()\nFIN()\n"))


def test_whitelist_rejects_double_star():
    assert any("**" in p for p in check_deck(GOOD))


# --- end to end (Code_Aster required) --------------------------------------------------------

needs_aster = pytest.mark.skipif(shutil.which("run_aster") is None, reason="Code_Aster not installed")
APP = Path("/app")


def _solve(task) -> dict:
    """Run the oracle script, which writes /app/mesh.mail, /app/model.comm and /app/answer.json."""
    for f in APP.glob("*"):
        if f.is_file():
            f.unlink()
    (APP / "oracle.py").write_text(task.oracle_model)
    subprocess.run(["python3", str(APP / "oracle.py")], cwd=APP, check=True, capture_output=True)
    return json.loads((APP / "answer.json").read_text())


@pytest.fixture(scope="module")
def cyl():
    return make_aster_cylinder(0)


@pytest.fixture(scope="module")
def hole():
    return make_aster_hole(1)


@needs_aster
@pytest.mark.parametrize("fixture", ["cyl", "hole"])
def test_oracle_scores_one(fixture, request):
    task = request.getfixturevalue(fixture)
    ans = _solve(task)
    r = verify_t0_aster(task.spec, task.meta, ans, APP)
    assert r["reward"] == pytest.approx(1.0), r
    assert all(q["rel_error"] < 0.005 for q in r["quantities"].values())


@needs_aster
def test_calc_champ_in_place_scores_one(hole):
    ans = _solve(hole)
    comm = APP / "model.comm"
    comm.write_text(comm.read_text().replace(
        "FIN()", "resu = CALC_CHAMP(reuse=resu, RESULTAT=resu, CONTRAINTE=('SIGM_ELNO',))\nFIN()"))
    r = verify_t0_aster(hole.spec, hole.meta, ans, APP)
    assert r["reward"] == pytest.approx(1.0), r


@needs_aster
def test_plane_stress_model_fails(hole):
    ans = _solve(hole)
    comm = APP / "model.comm"
    comm.write_text(comm.read_text().replace('MODELISATION="D_PLAN"', 'MODELISATION="C_PLAN"'))
    r = verify_t0_aster(hole.spec, hole.meta, ans, APP)
    assert r["reward"] < 0.5, r
    assert all(q["rel_error"] > 0.03 for q in r["quantities"].values())


@needs_aster
def test_injected_result_is_rejected(cyl):
    ans = _solve(cyl)
    comm = APP / "model.comm"
    comm.write_text(comm.read_text().replace("FIN()", "resu = CREA_RESU(OPERATION='AFFE', TYPE_RESU='EVOL_ELAS')\nFIN()"))
    r = verify_t0_aster(cyl.spec, cyl.meta, ans, APP)
    assert r["reward"] == 0.0 and "command file rejected" in r["flags"]


@needs_aster
def test_missing_node_at_point(cyl):
    ans = _solve(cyl)
    meta = json.loads(json.dumps(cyl.meta))
    meta["targets"]["u_inner"]["theta"] = 0.9   # between two nodes of the oracle mesh (1.875 deg apart)
    r = verify_t0_aster(cyl.spec, meta, ans, APP)
    assert any("no mesh node" in f for f in r["flags"])
    assert r["reward"] <= 0.5


@needs_aster
def test_inflated_claim_halves_score(cyl):
    ans = _solve(cyl)
    ans["quantities"] = {k: 1.2 * v for k, v in ans["quantities"].items()}
    r = verify_t0_aster(cyl.spec, cyl.meta, ans, APP)
    assert r["reward"] == pytest.approx(0.5)


@needs_aster
def test_broken_deck_scores_zero(cyl):
    ans = _solve(cyl)
    comm = APP / "model.comm"
    comm.write_text(comm.read_text().replace("MECA_STATIQUE(", "MECA_STATIQUE(BOGUS=1, "))
    r = verify_t0_aster(cyl.spec, cyl.meta, ans, APP)
    assert r["reward"] == 0.0 and "could not be solved" in r["flags"][0]
