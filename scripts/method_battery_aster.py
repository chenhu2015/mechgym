"""Method battery for the Code_Aster T0-A tasks (see scripts/method_battery.py for the idea).

    python scripts/method_battery_aster.py [--seeds 0 1 2 3 4 5] [--shard 0/1] [--out rows.json]

Uses the fixed /app directory of the aster-dev image, so variants run one after another; split the
work over several containers with --shard i/n and merge the JSON files with --report.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from mechgym.aster.problems import _oracle, make_t0_aster, verify_t0_aster

APP = Path("/app")
FULL = 0.01


def _sub(old: str, new: str, s: str) -> str:
    if old not in s:
        raise ValueError(f"pattern not found: {old!r}")
    return s.replace(old, new)


def tria3(script: str) -> str:
    s = _sub('L += ["FINSF", "QUAD4"]', 'L += ["FINSF", "TRIA3"]', script)
    return _sub('L.append(f" M{m} N{nid(i, j)} N{nid(i + 1, j)} N{nid(i + 1, j + 1)} N{nid(i, j + 1)}")',
                'L.append(f" M{m} N{nid(i, j)} N{nid(i + 1, j)} N{nid(i + 1, j + 1)}"); m += 1; '
                'L.append(f" M{m} N{nid(i, j)} N{nid(i + 1, j + 1)} N{nid(i, j + 1)}")', s)


def stat_non_line(script: str) -> str:
    return _sub('resu = MECA_STATIQUE(MODELE=model, CHAM_MATER=fmat, EXCIT=_F(CHARGE=load))',
                'times = DEFI_LIST_REEL(DEBUT=0.0, INTERVALLE=_F(JUSQU_A=1.0, NOMBRE=1))\n'
                'resu = STAT_NON_LINE(MODELE=model, CHAM_MATER=fmat, EXCIT=_F(CHARGE=load), '
                'COMPORTEMENT=_F(RELATION="ELAS"), INCREMENT=_F(LIST_INST=times))', script)


def formule_loads(script: str) -> str:
    """Far-field tractions through AFFE_CHAR_MECA_F with FORMULE constants passed as keywords."""
    s = _sub("""load = (f'FORCE_CONTOUR=(_F(GROUP_MA="RIGHT", FX={far_field[0]!r}), '
            f'_F(GROUP_MA="TOP", FY={far_field[1]!r})),')""",
             "load = ''", script)
    return _sub('resu = MECA_STATIQUE(MODELE=model, CHAM_MATER=fmat, EXCIT=_F(CHARGE=load))',
                'fx = FORMULE(VALE="SX + 0.0 * X", SX={far_field[0]!r}, NOM_PARA=("X", "Y"))\n'
                'fy = FORMULE(VALE="SY + 0.0 * Y", SY={far_field[1]!r}, NOM_PARA=("X", "Y"))\n'
                'trac = AFFE_CHAR_MECA_F(MODELE=model, FORCE_CONTOUR=(_F(GROUP_MA="RIGHT", FX=fx), '
                '_F(GROUP_MA="TOP", FY=fy)))\n'
                'resu = MECA_STATIQUE(MODELE=model, CHAM_MATER=fmat, EXCIT=(_F(CHARGE=load), _F(CHARGE=trac)))',
                s)


def variants(task):
    s = task.spec
    o = task.oracle_model
    if s["problem"] == "thick-cylinder":
        a, b, p = s["inner_radius"], s["outer_radius"], s["internal_pressure"]
        mk = lambda **kw: _oracle(a, b, s["youngs_modulus"], s["poisson_ratio"], pressure=p, **kw)
        extra = [("wrong", "pressure sign reversed", mk().replace(f"pressure, far_field = {p!r}, None",
                                                                  f"pressure, far_field = {-p!r}, None"))]
    else:
        a = s["hole_radius"]
        ff = (s["far_field_stress_x"], s["far_field_stress_y"])
        mk = lambda b=30.0 * a, **kw: _oracle(a, b, s["youngs_modulus"], s["poisson_ratio"], far_field=ff, **kw)
        extra = [("legit", "far-field tractions via FORMULE (keyword constants)", formule_loads(o)),
                 ("wrong", "domain 5a", mk(5.0 * a)),
                 ("wrong", "far-field stresses swapped",
                  _oracle(a, 30.0 * a, s["youngs_modulus"], s["poisson_ratio"], far_field=ff[::-1]))]
    # linear triangles at the quad density are borderline at the opening (mesh judgment): info
    tri_group = "legit" if s["problem"] == "thick-cylinder" else "info"
    return [
        ("legit", "oracle (QUAD4, quarter, MECA_STATIQUE)", o),
        (tri_group, "TRIA3 (each quad split in two)", tria3(o)),
        ("legit", "STAT_NON_LINE with ELAS", stat_non_line(o)),
        ("legit", "coarser mesh (30 x 24)", mk(n_r=30, n_t=24)),
        ("wrong", "plane stress (C_PLAN)", o.replace('MODELISATION="D_PLAN"', 'MODELISATION="C_PLAN"')),
        ("wrong", "very coarse mesh (4 x 4)", mk(n_r=4, n_t=4)),
        *extra,
    ]


def run(job):
    seed, group, name, script = job
    task = make_t0_aster(seed)
    for f in APP.glob("*"):
        if f.is_file():
            f.unlink()
        else:
            shutil.rmtree(f)
    (APP / "oracle.py").write_text(script)
    p = subprocess.run([sys.executable, "oracle.py"], cwd=APP, capture_output=True, text=True, timeout=1800)
    try:
        ans = json.loads((APP / "answer.json").read_text())
    except Exception:
        return dict(seed=seed, problem=task.spec["problem"], group=group, name=name, reward=0.0, ratio=None,
                    flags=["oracle script failed", p.stderr[-300:]])
    r = verify_t0_aster(task.spec, task.meta, ans, APP)
    errs = [max(q["rel_error"], q["surface_error"]) for q in r.get("quantities", {}).values()]
    return dict(seed=seed, problem=task.spec["problem"], group=group, name=name, reward=r["reward"],
                ratio=(max(errs) / FULL) if errs else None, flags=r.get("flags", [])[:3])


def report(rows):
    print("| problem | group | variant | rewards | error / full-credit tolerance (worst) | as expected |")
    print("|---|---|---|---|---|---|")
    keys = []
    for r in rows:
        k = (r["problem"], r["group"], r["name"])
        if k not in keys:
            keys.append(k)
    for k in keys:
        rs = [r for r in rows if (r["problem"], r["group"], r["name"]) == k]
        rewards = [r["reward"] for r in rs]
        ratios = [r["ratio"] for r in rs if r["ratio"] is not None]
        ok = (k[1] == "info" or (all(x >= 0.999 for x in rewards) if k[1] == "legit"
                                 else all(x < 0.999 for x in rewards)))
        worst = (min(ratios) if k[1] == "wrong" else max(ratios)) if ratios else None
        print(f"| {k[0]} | {k[1]} | {k[2]} | {' '.join(f'{x:.2f}' for x in rewards)} | "
              f"{'-' if worst is None else f'{worst:.2f}'} | {'yes' if ok else '**NO**'} |")
    for r in rows:
        if r["group"] != "info" and (r["group"] == "legit") != (r["reward"] >= 0.999):
            print(f"- unexpected: seed {r['seed']} {r['name']}: {r['reward']:.2f} {r['flags']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4, 5])
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--out", type=Path, default=Path("battery_aster.json"))
    ap.add_argument("--report", type=Path, nargs="*", help="merge shard outputs and print the table")
    args = ap.parse_args()
    if args.report:
        report([r for f in args.report for r in json.loads(f.read_text())])
        return
    i, n = map(int, args.shard.split("/"))
    jobs = [(seed, g, name, s) for seed in args.seeds for g, name, s in variants(make_t0_aster(seed))]
    rows = [run(j) for k, j in enumerate(jobs) if k % n == i]
    args.out.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
