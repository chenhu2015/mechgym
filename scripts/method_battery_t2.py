"""Method battery for T2 (slope design): how the score responds to legitimate design methods and to
the common mistakes.

    python scripts/method_battery_t2.py [--seeds 0 1 2 3 4 5] [--jobs 6]

Every design is found the way an engineer would find it: pick a family of regradings, analyze each
candidate with some method, and take the least excavation that meets FoS 1.3 by that method. The
reported `fos` is what that method gives for the chosen design, as an agent would report it.

Legitimate: the single-plane design (the reference family) with the stated analysis, its fos from a
coarser own analysis (30 slices), the same line with extra collinear vertices, a two-segment
(broken-back) face, and a design with a margin. Wrong: designing on a dry analysis when there is
groundwater, the ordinary method of slices (Fellenius), keeping the measured phreatic trace above
the new ground (instead of the stated lower of the two), and a toe buttress (fill).
"""

from __future__ import annotations

import argparse
import copy
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from mechgym.le import Circle, InvalidSurface, fellenius, make_slices, search_circular
from mechgym.spec import profile_from_spec
from mechgym.tasks import _cut_area, _design_profile, make_t2, verify_t2

TARGET = 1.3


def geometry(spec):
    s = spec["surface"]
    if s[1][1] < s[2][1]:                      # crest on the right: face looks toward -x
        return dict(toe=s[1], crest=s[2], sgn=1.0)
    return dict(toe=s[2], crest=s[1], sgn=-1.0)


def build(spec, top_x, knee=None):
    """Regraded ground: toe -> (optional knee) -> top point on the crest -> original end points."""
    s = spec["surface"]
    g = geometry(spec)
    top = [float(top_x), g["crest"][1]]
    face = [list(g["toe"])] + ([list(knee)] if knee is not None else []) + [top]
    if g["sgn"] < 0:
        face = face[::-1]
    return [list(map(float, p)) for p in [s[0]] + face + [s[-1]]]


def setback_limit(spec, meta):
    g = geometry(spec)
    a, b = meta["protected"]
    return (a if g["sgn"] > 0 else b) - g["crest"][0]   # signed distance to the road, times sgn


# --- analyses an agent might use -------------------------------------------------------------

def fos_bishop(spec, surf, n=60):
    return search_circular(_design_profile(spec, surf), "bishop", n_slices=n).fos


def fos_dry(spec, surf):
    return search_circular(profile_from_spec(dict(copy.deepcopy(spec), water_table=None), surface=surf),
                           "bishop").fos


def fos_unclipped(spec, surf):
    # the measured phreatic trace kept as is, even where it is now above the regraded ground
    return search_circular(profile_from_spec(spec, surface=surf), "bishop").fos


def fos_fellenius(spec, surf):
    prof = _design_profile(spec, surf)
    c = search_circular(prof, "bishop").surface
    best = np.inf
    for dx in np.arange(-3.0, 3.01, 1.5):
        for dz in np.arange(-3.0, 3.01, 1.5):
            for dr in np.arange(-3.0, 3.01, 1.0):
                try:
                    f = fellenius(prof, make_slices(prof, Circle(c.xc + dx, c.zc + dz, c.r + dr), 60))
                except (InvalidSurface, ValueError):
                    continue
                if f > 0:
                    best = min(best, f)
    return best


def least_cut(spec, meta, fos, family, target=TARGET, iters=10):
    """Smallest setback d (0..road) for which fos(family(d)) >= target, by bisection."""
    g = geometry(spec)
    lim = abs(setback_limit(spec, meta))
    mk = lambda d: family(g["crest"][0] + g["sgn"] * d)
    if fos(spec, mk(lim)) < target:
        return None
    lo, hi = 0.0, lim
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        lo, hi = (lo, mid) if fos(spec, mk(mid)) >= target else (mid, hi)
    surf = mk(hi)
    return surf, fos(spec, surf)


def broken_back(spec, frac, shift):
    """Face with a knee at `frac` of the height, moved `shift` x height horizontally into the slope
    from the straight toe-top line (negative: out of the slope, i.e. a convex face)."""
    g = geometry(spec)
    h = g["crest"][1] - g["toe"][1]

    def family(top_x):
        x_line = g["toe"][0] + frac * (top_x - g["toe"][0])
        return build(spec, top_x, [x_line + g["sgn"] * shift * h, g["toe"][1] + frac * h])
    return family


def variants(seed):
    t = make_t2(seed)
    spec, meta = t.spec, t.meta
    plane = lambda top_x: build(spec, top_x)
    out = []

    def add(group, name, res, claim=None):
        if res is None:
            out.append((group, name, None))
            return
        surf, f = res
        out.append((group, name, {"surface": surf, "fos": f if claim is None else claim(surf)}))

    p = least_cut(spec, meta, fos_bishop, plane)
    add("legit", "single plane, stated analysis", p)
    add("legit", "single plane, fos from own 30-slice analysis", p, lambda s: fos_bishop(spec, s, 30))
    if p is not None:
        surf = np.array(p[0])
        dense = [list(surf[0])]
        for a, b in zip(surf[:-1], surf[1:]):
            dense += [list(a + (b - a) * k / 7.0) for k in range(1, 8)]
        out.append(("legit", "same line, 7x collinear vertices", {"surface": dense, "fos": p[1]}))
    best = None
    for frac in (0.5,):
        for shift in (-0.15, -0.08, 0.08, 0.15):
            r = least_cut(spec, meta, fos_bishop, broken_back(spec, frac, shift))
            if r is not None and (best is None or _cut_area(spec["surface"], r[0]) <
                                  _cut_area(spec["surface"], best[0])):
                best = r
    add("info", "two-segment face, best of 4 knee positions", best)
    add("info", "single plane designed to FoS 1.33 (margin)", least_cut(spec, meta, fos_bishop, plane, 1.33))
    if spec.get("water_table") is not None:
        add("wrong", "designed on a dry analysis (groundwater ignored)", least_cut(spec, meta, fos_dry, plane))
        add("info", "phreatic trace kept above the new ground", least_cut(spec, meta, fos_unclipped, plane))
    add("wrong", "designed with Fellenius (ordinary method of slices)", least_cut(spec, meta, fos_fellenius, plane))
    if p is not None:
        surf = [list(x) for x in p[0]]
        g = geometry(spec)
        # 3 m wide, 1.5 m high toe berm in front of the toe on the design line
        xt, zt = g["toe"]
        i = next(k for k, q in enumerate(surf) if abs(q[0] - xt) < 1e-9)
        berm = [[xt - g["sgn"] * 3.0, zt], [xt - g["sgn"] * 3.0, zt + 1.5], [xt, zt + 1.5]]
        berm[0][0] += -g["sgn"] * 1e-3
        pts = surf[:i] + (berm if g["sgn"] > 0 else berm[::-1]) + surf[i + 1:]
        pts = sorted(pts, key=lambda q: q[0])
        out.append(("wrong", "toe berm of fill on the design line", {"surface": pts, "fos": p[1]}))
    rows = []
    for g_, n, ans in out:
        r = verify_t2(spec, meta, ans) if ans is not None else None
        rows.append(dict(seed=seed, group=g_, name=n, water=spec.get("water_table") is not None, r=r,
                         claimed=None if ans is None else ans["fos"]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4, 5])
    ap.add_argument("--jobs", type=int, default=6)
    args = ap.parse_args()
    with ProcessPoolExecutor(args.jobs) as ex:
        rows = [r for rs in ex.map(variants, args.seeds) for r in rs]
    print("water table present on seeds:", [s for s in args.seeds
                                            if any(r["water"] for r in rows if r["seed"] == s)])
    print("| group | variant | seed: reward (re-analyzed FoS / claimed / area ratio ref:design) | as expected |")
    print("|---|---|---|---|")
    keys = []
    for r in rows:
        if (r["group"], r["name"]) not in keys:
            keys.append((r["group"], r["name"]))
    for g, n in keys:
        rs = [r for r in rows if (r["group"], r["name"]) == (g, n)]
        cells = []
        for x in rs:
            v = x["r"]
            if v is None:
                cells.append(f"{x['seed']}: no feasible design")
            elif "fos_verified" not in v:
                cells.append(f"{x['seed']}: {v['reward']:.2f} ({'; '.join(v['flags'])})")
            else:
                cells.append(f"{x['seed']}: {v['reward']:.2f} ({v['fos_verified']:.3f} / {x['claimed']:.3f} / "
                             f"{v['efficiency_uncapped']:.2f})")
        rew = [x["r"]["reward"] for x in rs if x["r"] is not None]
        ok = g == "info" or (all(v >= 0.98 for v in rew) if g == "legit" else all(v < 0.98 for v in rew))
        print(f"| {g} | {n} | {'; '.join(cells)} | {'yes' if ok else '**NO**'} |")


if __name__ == "__main__":
    main()
