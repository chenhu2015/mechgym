"""Method battery for the OpenSees T0 tasks: legitimate alternatives must pass, plausible mistakes must fail.

    python scripts/method_battery.py [--seeds 0 1 2] [--jobs 8] [--only cyl,hole,foot] [--out battery.json]

Each variant is a modification of the oracle model that an engineer could reasonably write. A
*legitimate* variant (another element type, a full instead of a quarter model, a square instead of a
circular plate, another mesh density, a rough instead of a smooth footing) must earn full credit; a
*wrong* variant (plane stress, a domain that is too small, a mesh that is too coarse, a locking
element, a sign error) must not. The report gives, per variant, the reward and the error relative
to the full-credit tolerance on every seed, so the margin between the two groups is visible.

Runs OpenSeesPy, so run it in the geogym-dev image.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from mechgym.fea.problems import (FOOT_FULL, _oracle_annulus, _oracle_footing, make_circular_hole,
                                  make_strip_footing, make_thick_cylinder, verify_t0)

ELASTIC_FULL = 0.01   # full-credit relative error for the elastic problems

# ---------------------------------------------------------------------------------------------
# script transformations
# ---------------------------------------------------------------------------------------------

_QUAD = re.compile(r'k \+= 1\n(\s*)ops\.element\("quad", k, (tag\(i, j\)), (tag\(i \+ 1, j\)), '
                   r'(tag\(i \+ 1, j \+ 1\)), (tag\(i, j \+ 1\)),\n\s*1\.0, "PlaneStrain", 1\)')


def _sub(pattern, repl, s: str) -> str:
    out, n = (pattern.subn(repl, s) if hasattr(pattern, "subn") else (s.replace(pattern, repl), s.count(pattern)))
    if n == 0:
        raise ValueError(f"pattern not found: {pattern!r}")
    return out


def annulus_element(script: str, kind: str) -> str:
    def repl(m):
        ind, n1, n2, n3, n4 = m.groups()
        if kind == "tri31":
            return (f'k += 1\n{ind}ops.element("tri31", k, {n1}, {n2}, {n3}, 1.0, "PlaneStrain", 1)\n'
                    f'{ind}k += 1\n{ind}ops.element("tri31", k, {n1}, {n3}, {n4}, 1.0, "PlaneStrain", 1)')
        if kind == "SSPquad":
            return f'k += 1\n{ind}ops.element("SSPquad", k, {n1}, {n2}, {n3}, {n4}, 1, "PlaneStrain", 1.0)'
        if kind == "bbarQuad":
            return f'k += 1\n{ind}ops.element("bbarQuad", k, {n1}, {n2}, {n3}, {n4}, 1.0, 1)'
        return f'k += 1\n{ind}ops.element("{kind}", k, {n1}, {n2}, {n3}, {n4}, 1.0, "PlaneStrain", 1)'
    return _sub(_QUAD, repl, script)


def annulus_square_plate(script: str) -> str:
    """Map the outer ring onto the square |x|, |y| <= b (rays from the centre, geometric grading)."""
    return _sub('ops.node(tag(i, j), r * np.cos(t), r * np.sin(t))',
                'rb = b / max(np.cos(t), np.sin(t)); rr = a * (rb / a) ** (i / nr); '
                'ops.node(tag(i, j), rr * np.cos(t), rr * np.sin(t))', script)


def annulus_full(script: str) -> str:
    """Full 360-degree ring instead of a quarter; rigid-body modes removed by symmetric supports."""
    s = _sub("thetas = np.linspace(0.0, np.pi / 2, nt + 1)",
             "nt = 4 * nt\nthetas = np.linspace(0.0, 2 * np.pi, nt + 1)[:-1]", script)
    s = _sub("tag = lambda i, j: 1 + i * (nt + 1) + j", "tag = lambda i, j: 1 + i * nt + (j % nt)", s)
    s = _sub("""for i in range(nr + 1):
    ops.fix(tag(i, 0), 0, 1)     # theta = 0: u_y = 0
    ops.fix(tag(i, nt), 1, 0)    # theta = 90: u_x = 0""",
             """for i in range(nr + 1):
    ops.fix(tag(i, 0), 0, 1)
    ops.fix(tag(i, nt // 2), 0, 1)
    ops.fix(tag(i, nt // 4), 1, 0)
    ops.fix(tag(i, 3 * nt // 4), 1, 0)""", s)
    s = _sub('q = {"u_inner": tag(0, nt // 2), "u_outer": tag(nr, nt // 2)}',
             'q = {"u_inner": tag(0, nt // 8), "u_outer": tag(nr, nt // 8)}', s)
    s = _sub('q = {"u_r_theta0": tag(0, 0), "u_r_theta90": tag(0, nt)}',
             'q = {"u_r_theta0": tag(0, 0), "u_r_theta90": tag(0, nt // 4)}', s)
    return s


def footing_element(script: str, kind: str) -> str:
    old = 'ops.element("bbarQuad", e, tag(i, j), tag(i + 1, j), tag(i + 1, j + 1), tag(i, j + 1), 1.0, 1)'
    nodes = "tag(i, j), tag(i + 1, j), tag(i + 1, j + 1), tag(i, j + 1)"
    new = {
        "quad": f'ops.element("quad", e, {nodes}, 1.0, "PlaneStrain", 1)',
        "enhancedQuad": f'ops.element("enhancedQuad", e, {nodes}, 1.0, "PlaneStrain", 1)',
        "SSPquad": f'ops.element("SSPquad", e, {nodes}, 1, "PlaneStrain", 1.0)',
        "tri31": (f'ops.element("tri31", e, tag(i, j), tag(i + 1, j), tag(i + 1, j + 1), 1.0, "PlaneStrain", 1); '
                  f'e += 1; ops.element("tri31", e, tag(i, j), tag(i + 1, j + 1), tag(i, j + 1), 1.0, "PlaneStrain", 1)'),
    }[kind]
    return _sub(old, new, script)


def footing_rough(script: str) -> str:
    return _sub("for n in foot:\n    ops.sp(n, 2, -delta)",
                "for n in foot[1:]:\n    ops.fix(n, 1, 0)\nfor n in foot:\n    ops.sp(n, 2, -delta)",
                script)


def footing_full(script: str) -> str:
    s = _sub("ys = [0.0]; dy = h",
             "xs = sorted(set([-x for x in xs] + xs))\nys = [0.0]; dy = h", script)
    s = _sub("foot = [tag(i, ny) for i in range(nf + 1)]",
             "foot = [tag(i, ny) for i, x in enumerate(xs) if abs(x) <= B / 2 + 1e-9]", s)
    s = _sub("/ (B / 2))", "/ B)", s)
    s = _sub('"half_model": True', '"half_model": False', s)
    return s


# ---------------------------------------------------------------------------------------------
# variants
# ---------------------------------------------------------------------------------------------


def cyl_variants(t):
    s = t.spec
    a, b, e, nu, p = s["inner_radius"], s["outer_radius"], s["youngs_modulus"], s["poisson_ratio"], s["internal_pressure"]
    o = t.oracle_model
    mk = lambda **kw: _oracle_annulus(a, b, e, nu, inner_pressure=p, outer="free", **kw)
    return [
        ("legit", "oracle (quad, quarter)", o),
        ("legit", "enhancedQuad", annulus_element(o, "enhancedQuad")),
        ("legit", "SSPquad", annulus_element(o, "SSPquad")),
        ("legit", "bbarQuad", annulus_element(o, "bbarQuad")),
        ("legit", "tri31 (each quad split in two)", annulus_element(o, "tri31")),
        ("legit", "full ring", annulus_full(o)),
        ("legit", "coarser mesh 24 x 20", mk(n_theta=24, n_r=20)),
        ("legit", "finer mesh 96 x 120", mk(n_theta=96, n_r=120)),
        ("wrong", "plane stress", o.replace('"PlaneStrain"', '"PlaneStress"')),
        ("wrong", "very coarse mesh 4 x 3", mk(n_theta=4, n_r=3)),
        ("wrong", "pressure sign reversed", _sub("edge(0, lambda nx, ny: (-p_in * nx, -p_in * ny))",
                                                  "edge(0, lambda nx, ny: (p_in * nx, p_in * ny))", o)),
    ]


def hole_variants(t):
    s = t.spec
    a, e, nu, sx, sy = s["hole_radius"], s["youngs_modulus"], s["poisson_ratio"], s["far_field_stress_x"], s["far_field_stress_y"]
    o = t.oracle_model
    mk = lambda b, **kw: _oracle_annulus(a, b * a, e, nu, inner_pressure=0.0, outer=("stress", sx, sy), **kw)
    return [
        ("legit", "oracle (quad, quarter ring to 30a)", o),
        ("legit", "enhancedQuad", annulus_element(o, "enhancedQuad")),
        ("legit", "SSPquad", annulus_element(o, "SSPquad")),
        # constant-strain triangles at the quad mesh's density are too stiff at the opening for
        # some instances: whether the mesh is fine enough is part of the task
        ("info", "tri31 at the oracle's mesh density", annulus_element(o, "tri31")),
        ("legit", "full ring", annulus_full(o)),
        ("legit", "square plate, half-width 30a", annulus_square_plate(o)),
        # domain truncation error at 20a is about 1-1.5% depending on the stress ratio: borderline
        ("info", "ring to 20a", mk(20.0)),
        ("legit", "ring to 60a, finer", mk(60.0, n_theta=64, n_r=90)),
        ("wrong", "plane stress", o.replace('"PlaneStrain"', '"PlaneStress"')),
        ("wrong", "domain 3a", mk(3.0)),
        ("wrong", "domain 6a", mk(6.0)),
        ("wrong", "very coarse mesh 4 x 6", mk(30.0, n_theta=4, n_r=6)),
        ("wrong", "far-field stresses swapped",
         _oracle_annulus(a, 30.0 * a, e, nu, inner_pressure=0.0, outer=("stress", sy, sx))),
    ]


def foot_variants(t):
    s = t.spec
    b, su, e, nu = s["footing_width"], s["undrained_strength"], s["youngs_modulus"], s["poisson_ratio"]
    o = t.oracle_model
    mk = lambda **kw: _oracle_footing(b, su, e, nu, **kw)
    return [
        ("legit", "oracle (bbarQuad, half, smooth)", o),
        # element choices that do not work for this problem; the agent's own run shows the same
        # (enhancedQuad + J2: singular at the first step; SSPquad: about 7% above the collapse load)
        ("info", "enhancedQuad", footing_element(o, "enhancedQuad")),
        ("info", "SSPquad", footing_element(o, "SSPquad")),
        # not the stated (smooth) problem; same exact load, 3.2% above it on this mesh
        ("info", "rough footing", footing_rough(o)),
        ("legit", "full model", footing_full(o)),
        ("legit", "finer mesh (30 elements under half footing)", mk(nf=30)),
        ("legit", "smaller domain 3B", mk(extent=3.0)),
        ("wrong", "plain quad (locking)", footing_element(o, "quad")),
        ("wrong", "tri31 (locking)", footing_element(o, "tri31")),
        ("wrong", "very coarse mesh (3 elements under half footing)", mk(nf=3)),
        ("wrong", "settlement too small", o.replace("delta = max(0.03, 25.0 * su / E) * B",
                                                    "delta = 0.5 * su / E * B")),
    ]


FAMILIES = {"cyl": (make_thick_cylinder, cyl_variants), "hole": (make_circular_hole, hole_variants),
            "foot": (make_strip_footing, foot_variants)}


def run_one(job):
    fam, seed, group, name, script = job
    task = FAMILIES[fam][0](seed)
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "model.py").write_text(script)
        p = subprocess.run([sys.executable, "model.py"], cwd=d, capture_output=True, text=True, timeout=1800)
        try:
            ans = json.loads((d / "answer.json").read_text())
        except Exception:
            return dict(fam=fam, seed=seed, group=group, name=name, reward=0.0,
                        err=None, flags=["script wrote no answer", p.stderr[-300:]])
        r = verify_t0(task.spec, task.meta, ans, d)
    if fam == "foot":
        err = r.get("rel_error")
        ratio = None if err is None else err / FOOT_FULL
    else:
        errs = [max(q["rel_error"], q["surface_error"]) for q in r.get("quantities", {}).values()]
        err = max(errs) if errs else None
        ratio = None if err is None else err / ELASTIC_FULL
    return dict(fam=fam, seed=seed, group=group, name=name, reward=r.get("reward", 0.0),
                err=err, ratio=ratio, flags=r.get("flags", [])[:3])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--only", default="cyl,hole,foot")
    ap.add_argument("--out", type=Path, default=Path("battery.json"))
    args = ap.parse_args()
    jobs = []
    for fam in args.only.split(","):
        make, variants = FAMILIES[fam]
        for seed in args.seeds:
            for group, name, script in variants(make(seed)):
                jobs.append((fam, seed, group, name, script))
    with ProcessPoolExecutor(args.jobs) as ex:
        rows = list(ex.map(run_one, jobs))
    args.out.write_text(json.dumps(rows, indent=1))

    print("| task | group | variant | rewards | error / full-credit tolerance (worst) | as expected |")
    print("|---|---|---|---|---|---|")
    seen = []
    for r in rows:
        k = (r["fam"], r["group"], r["name"])
        if k not in seen:
            seen.append(k)
    for fam, group, name in seen:
        rs = [r for r in rows if (r["fam"], r["group"], r["name"]) == (fam, group, name)]
        rewards = [r["reward"] for r in rs]
        ratios = [r["ratio"] for r in rs if r.get("ratio") is not None]
        if group == "info":
            ok = True
            worst = max(ratios) if ratios else None
        elif group == "legit":
            ok = all(x >= 1.0 - 1e-9 for x in rewards)
            worst = max(ratios) if ratios else None
        else:
            ok = all(x < 1.0 for x in rewards)
            worst = min(ratios) if ratios else None
        print(f"| {fam} | {group} | {name} | {' '.join(f'{x:.2f}' for x in rewards)} | "
              f"{'-' if worst is None else f'{worst:.2f}'} | {'yes' if ok else '**NO**'} |")
    bad = [r for r in rows if r["group"] != "info" and (r["group"] == "legit") != (r["reward"] >= 1.0 - 1e-9)]
    if bad:
        print("\nunexpected:")
        for r in bad:
            print(f"- {r['fam']} seed {r['seed']} {r['group']} {r['name']}: reward {r['reward']:.2f}, "
                  f"err {r['err']}, flags {r['flags']}")


if __name__ == "__main__":
    main()
