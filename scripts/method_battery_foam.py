"""Method battery for the OpenFOAM T0-F task (see scripts/method_battery.py for the idea).

    python scripts/method_battery_foam.py [--seeds 0 1 2] [--jobs 12]

Each variant is a case written with the oracle's case writer and different choices. The verifier
runs every case itself; the reported reward assumes the agent reports the values its own case gives
(the consistency factor is not what is being tested here).
"""

from __future__ import annotations

import argparse
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from mechgym.foam import problems as fp
from mechgym.foam.case import write_kirsch_case

FULL = 0.01
ORACLE = dict(n_ring=60, n_quarter=80, n_far=120, far_grading=40.0, end_time=8000)


def variants(s):
    a = s["hole_radius"]
    base = dict(a=a, L=80.0 * a, sx=s["far_field_stress_x"], sy=s["far_field_stress_y"],
                e=s["youngs_modulus"], nu=s["poisson_ratio"], **ORACLE)
    v = lambda **kw: {**base, **kw}
    return [
        ("legit", "oracle (80a, 60/80/120 cells, 8000 iterations)", v(), None),
        ("legit", "plate 60a", v(L=60.0 * a), None),
        ("legit", "more iterations (12000)", v(end_time=12000), None),
        ("legit", "different density (rho = 2000)", v(rho=2000.0), None),
        ("legit", "binary output", v(), ("system/controlDict", "writeFormat ascii;", "writeFormat binary;")),
        ("info", "plate 40a", v(L=40.0 * a), None),
        ("wrong", "plane stress", v(plane_stress=True), None),
        ("wrong", "plate 10a", v(L=10.0 * a), None),
        # meant as an unconverged case, but 300 iterations already converge on this mesh (measured):
        # the iteration count is not a gate here
        ("info", "300 iterations", v(end_time=300), None),
        ("wrong", "far-field stresses swapped", v(sx=s["far_field_stress_y"], sy=s["far_field_stress_x"]), None),
    ]


def run(job):
    seed, group, name, kw, edit = job
    task = fp.make_foam_hole(seed)
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp)
        write_kirsch_case(app / "case", **kw)
        if edit:
            f = app / "case" / edit[0]
            txt = f.read_text()
            if edit[1] not in txt:
                return dict(seed=seed, group=group, name=name, reward=None, ratio=None, flags=["edit not applied"])
            f.write_text(txt.replace(edit[1], edit[2]))
        dummy = {"quantities": {k: 0.0 for k in task.meta["targets"]}}
        r = fp.verify_t0_foam(task.spec, task.meta, dummy, app)
    qs = r.get("quantities", {})
    if not qs:
        return dict(seed=seed, group=group, name=name, reward=0.0, ratio=None, flags=r.get("flags", [])[:3])
    errs = [max(q["rel_error"], q["surface_error"]) for q in qs.values()]
    honest = sum(min(fp._lin(q["rel_error"]), fp._lin(q["surface_error"])) for q in qs.values()) / len(task.meta["targets"])
    return dict(seed=seed, group=group, name=name, reward=honest, ratio=max(errs) / FULL,
                flags=[f for f in r.get("flags", []) if "reported value" not in f][:3])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--jobs", type=int, default=12)
    args = ap.parse_args()
    jobs = [(seed, g, n, kw, e) for seed in args.seeds for g, n, kw, e in variants(fp.make_foam_hole(seed).spec)]
    with ProcessPoolExecutor(args.jobs) as ex:
        rows = list(ex.map(run, jobs))
    print("| group | variant | rewards (honest claims) | error / full-credit tolerance (worst) | as expected |")
    print("|---|---|---|---|---|")
    keys = []
    for r in rows:
        if (r["group"], r["name"]) not in keys:
            keys.append((r["group"], r["name"]))
    for g, n in keys:
        rs = [r for r in rows if (r["group"], r["name"]) == (g, n)]
        rewards = [r["reward"] for r in rs]
        ratios = [r["ratio"] for r in rs if r["ratio"] is not None]
        ok = g == "info" or (all(x is not None and x >= 0.999 for x in rewards) if g == "legit"
                             else all(x is not None and x < 0.999 for x in rewards))
        worst = (min(ratios) if g == "wrong" else max(ratios)) if ratios else None
        cells = " ".join("-" if x is None else f"{x:.2f}" for x in rewards)
        print(f"| {g} | {n} | {cells} | {'-' if worst is None else f'{worst:.2f}'} | {'yes' if ok else '**NO**'} |")
    for r in rows:
        if r["flags"]:
            print(f"- seed {r['seed']} {r['name']}: {r['flags']}")


if __name__ == "__main__":
    main()
