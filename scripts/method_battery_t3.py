"""Method battery for T3 (back-analysis): how much the non-uniqueness of (c', phi') costs, and
whether the common mistakes fail.

    python scripts/method_battery_t3.py [--seeds 0 1 2]

Legitimate: for phi' within a few degrees of the true value, choose c' so that the observed slip
surface has FoS = 1, and predict the drained FoS with those parameters (the standard
back-analysis when only one failure is observed). Wrong: the laboratory peak values; a back-analysis
that ignores the pore pressures at failure.
"""

from __future__ import annotations

import argparse
import copy

from mechgym.le import Circle, evaluate
from mechgym.spec import profile_from_spec
from mechgym.tasks import _critical, _with_strength, make_t3, verify_t3


def c_for_unit_fos(base, circle, phi, lo=0.0, hi=200.0):
    f = lambda c: evaluate(profile_from_spec(_with_strength(base, c, phi)), circle, "bishop")
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if f(mid) < 1.0 else (lo, mid)
    return 0.5 * (lo + hi)


def answer(base, meta, c, phi):
    f = _critical(_with_strength(base, c, phi, meta["wt_drained"])).fos
    return {"c": c, "phi_deg": phi, "fos_drained": f}


def variants(t):
    base = {k: v for k, v in t.spec.items() if k != "water_table_drained"}
    fc = t.meta["failure_circle_internal"]
    circ = Circle(fc["xc"], fc["zc"], fc["r"])
    phi_t = t.meta["phi_true"]
    out = [("legit", "true parameters", t.reference)]
    for d in (-3.0, -1.5, 1.5, 3.0):
        phi = phi_t + d
        out.append(("legit" if abs(d) <= 1.5 else "info", f"phi' {d:+.1f} deg, c' for FoS = 1 on the observed surface",
                    answer(base, t.meta, c_for_unit_fos(base, circ, phi), phi)))
    m = t.spec["layers"][0]["material"]
    out.append(("wrong", "laboratory peak values", answer(base, t.meta, m["c"], m["phi_deg"])))
    out.append(("info", "laboratory phi', c' back-calculated",
                answer(base, t.meta, c_for_unit_fos(base, circ, m["phi_deg"]), m["phi_deg"])))
    dry = dict(copy.deepcopy(base), water_table=None)
    c_dry = c_for_unit_fos(dry, circ, phi_t)
    out.append(("wrong", "back-analysis ignoring pore pressure at failure (true phi')",
                answer(base, t.meta, c_dry, phi_t)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    args = ap.parse_args()
    rows = []
    for seed in args.seeds:
        t = make_t3(seed)
        for g, n, a in variants(t):
            r = verify_t3(t.spec, t.meta, a)
            rows.append((g, n, seed, r, a))
    print("| group | variant | seed: reward (fit / location / prediction) |")
    print("|---|---|---|")
    keys = []
    for g, n, *_ in rows:
        if (g, n) not in keys:
            keys.append((g, n))
    for g, n in keys:
        cells = [f"{s}: {r['reward']:.2f} ({r.get('score_fit', 0):.2f}/{r.get('score_location', 0):.2f}/"
                 f"{r.get('score_prediction', 0):.2f})" for gg, nn, s, r, _ in rows if (gg, nn) == (g, n)]
        print(f"| {g} | {n} | {'; '.join(cells)} |")


if __name__ == "__main__":
    main()
