"""Method battery for T1 (slope stability check): legitimate variations must pass, mistakes must fail.

    python scripts/method_battery_le.py [--seeds 0 1 2 3 4 5]

Legitimate: the critical surface found by the verifier's own search; the same surface with the
factor of safety computed with fewer or more slices (an agent's own Bishop code); a nearby surface
found by a coarse search. Wrong: ignoring the phreatic surface; the ordinary method of slices
(Fellenius) instead of Bishop; a circle whose lower arc does not daylight on the crest side.
"""

from __future__ import annotations

import argparse
import copy

import numpy as np

from mechgym.le import Circle, InvalidSurface, bishop, evaluate, make_slices, search_circular
from mechgym.le import fellenius as le_fellenius
from mechgym.spec import profile_from_spec
from mechgym.tasks import _to_internal_circle, _to_site_circle, make_t1, verify_t1


def fellenius(prof, circ, n=60):
    return le_fellenius(prof, make_slices(prof, circ, n))


def coarse_grid(prof, ref: Circle, f_ref: float):
    """Best circle on a 1 m grid of centres around the reference with the radius searched on 0.25 m."""
    best = None
    for dx in np.arange(-3.0, 3.01, 1.0):
        for dz in np.arange(-3.0, 3.01, 1.0):
            for dr in np.arange(-3.0, 3.01, 0.25):
                c = Circle(ref.xc + dx, ref.zc + dz, ref.r + dr)
                try:
                    f = evaluate(prof, c, "bishop")
                except (InvalidSurface, ValueError):
                    continue
                if best is None or f < best[0]:
                    best = (f, c)
    return best


def variants(task):
    prof = profile_from_spec(task.spec)
    ref = task.reference
    circ = _to_internal_circle(prof, ref["slip_surface"])
    out = [("legit", "oracle (verifier search, 60 slices)", ref)]
    for n in (15, 30, 200):
        s = make_slices(prof, circ, n)
        # 15 slices is coarser than the statement asks for (a converged slicing): informational
        out.append(("legit" if n >= 30 else "info", f"same surface, FoS from {n} slices",
                    dict(ref, fos=bishop(prof, s))))
    # a circle off the reference grid: centre moved 1.5 m, best radius on a 0.25 m grid
    shifted = Circle(circ.xc + 1.5, circ.zc + 1.5, circ.r)
    best = None
    for dr in np.arange(-4.0, 4.01, 0.25):
        c = Circle(shifted.xc, shifted.zc, circ.r + dr)
        try:
            f = evaluate(prof, c, "bishop")
        except (InvalidSurface, ValueError):
            continue
        if best is None or f < best[0]:
            best = (f, c)
    if best:
        # not a legitimate answer (it is not the critical surface): shows how sharp the minimum is
        out.append(("info", "centre 1.5 m off the critical one, best radius",
                    {"fos": best[0], "slip_surface": _to_site_circle(prof, best[1])}))
    g = coarse_grid(prof, circ, ref["fos"])
    if g:
        out.append(("legit", "coarse grid search (1 m centres, 0.25 m radii)",
                    {"fos": g[0], "slip_surface": _to_site_circle(prof, g[1])}))

    if task.spec.get("water_table") is not None:
        dry = profile_from_spec(dict(copy.deepcopy(task.spec), water_table=None))
        r = search_circular(dry, "bishop")
        out.append(("wrong", "phreatic surface ignored (dry analysis)",
                    {"fos": r.fos, "slip_surface": _to_site_circle(prof, r.surface)}))
    out.append(("wrong", "ordinary method of slices (Fellenius) on the critical circle",
                dict(ref, fos=fellenius(prof, circ))))
    # Fellenius search: the circle minimizing the Fellenius FoS, reported with its Fellenius value
    best = None
    for dx in np.arange(-3.0, 3.01, 1.0):
        for dz in np.arange(-3.0, 3.01, 1.0):
            for dr in np.arange(-3.0, 3.01, 0.5):
                c = Circle(circ.xc + dx, circ.zc + dz, circ.r + dr)
                try:
                    f = fellenius(prof, c)
                except (InvalidSurface, ValueError):
                    continue
                if f > 0 and (best is None or f < best[0]):
                    best = (f, c)
    if best:
        out.append(("wrong", "Fellenius search and value",
                    {"fos": best[0], "slip_surface": _to_site_circle(prof, best[1])}))
    # lower the centre until the lower arc no longer daylights
    for k in np.arange(0.5, 30.0, 0.5):
        c = Circle(circ.xc, circ.zc - k, circ.r - k)
        try:
            evaluate(prof, c, "bishop")
        except (InvalidSurface, ValueError) as e:
            if "domain" in str(e):
                out.append(("wrong", "lower arc does not daylight",
                            {"fos": ref["fos"], "slip_surface": _to_site_circle(prof, c)}))
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4, 5])
    args = ap.parse_args()
    rows = []
    for seed in args.seeds:
        t = make_t1(seed)
        for group, name, ans in variants(t):
            r = verify_t1(t.spec, t.meta, ans)
            rows.append((group, name, seed, r))
    print("| group | variant | seed: reward (FoS of surface / claimed / reference) |")
    print("|---|---|---|")
    names = []
    for g, n, _, _ in rows:
        if (g, n) not in names:
            names.append((g, n))
    for g, n in names:
        cells = []
        for gg, nn, seed, r in rows:
            if (gg, nn) != (g, n):
                continue
            fs = r.get("fos_of_reported_surface")
            cells.append(f"{seed}: {r['reward']:.2f} ({'-' if fs is None else f'{fs:.3f}'} / "
                         f"{r.get('fos_claimed', float('nan')):.3f} / {r['fos_reference']:.3f})")
        rewards = [r["reward"] for gg, nn, _, r in rows if (gg, nn) == (g, n)]
        ok = (g == "info" or (all(x >= 0.999 for x in rewards) if g == "legit"
                               else all(x < 0.999 for x in rewards)))
        print(f"| {g} | {n}{'' if ok else ' **UNEXPECTED**'} | {'; '.join(cells)} |")


if __name__ == "__main__":
    main()
