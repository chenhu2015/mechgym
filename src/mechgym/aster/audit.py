"""Admissibility audit of a Code_Aster command file: is it the stated problem?

:func:`mechgym.aster.deck.check_deck` keeps the deck from computing or injecting a result field. This
audit closes the remaining route, injecting the answer through the *model*: a nonzero imposed
displacement at the graded nodes, discrete springs, a wrong or tuned material, or loads placed
somewhere other than where the statement applies them. Static checks (on the syntax tree):

- every ``AFFE_MODELE`` assignment uses ``MODELISATION="D_PLAN"`` (plane-strain continuum; no
  discrete elements, so no springs);
- ``DEFI_MATERIAU`` defines only ``ELAS`` with the stated ``E`` and ``NU`` (within 0.5%), given as
  numeric constants;
- ``AFFE_CHAR_MECA`` uses only ``DDL_IMPO`` / ``FACE_IMPO`` with all values 0 (supports, symmetry)
  and ``PRES_REP`` / ``FORCE_CONTOUR`` on cell groups named by string literals; ``AFFE_CHAR_CINE``
  only zero ``MECA_IMPO``; ``AFFE_CHAR_MECA_F`` only for those loads; ``AFFE_CARA_ELEM`` is rejected.

:func:`check_load_groups` then checks, on the mesh the deck actually read, that the loaded groups
lie where the statement applies loads, and :func:`check_applied_loads` that the assembled load
vector is statically equivalent to the stated traction (:mod:`mechgym.loadcheck`); red-team agents
otherwise applied the exact Kirsch traction on a small domain through a FORMULE.
"""

from __future__ import annotations

import ast
import math

REL_TOL = 0.005
GEOM_TOL = 0.005
CHAR_KEYS = {"MODELE", "DDL_IMPO", "FACE_IMPO", "PRES_REP", "FORCE_CONTOUR", "INFO", "VERI_NORM"}
LOAD_KEYS = {"PRES_REP", "FORCE_CONTOUR"}
DISP_COMPONENTS = {"DX", "DY", "DZ", "DRX", "DRY", "DRZ", "DNOR", "DTAN"}
MED_CORNERS = {203: 3, 206: 3, 207: 3, 204: 4, 208: 4, 209: 4}   # 2D MED cell types


class _Unknown(Exception):
    pass


def _eval(node: ast.AST, env: dict) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand, env)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, env), _eval(node.right, env)
        ops = {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b}
        if type(node.op) in ops:
            return ops[type(node.op)]
        if isinstance(node.op, ast.Div) and b != 0:
            return a / b
        if isinstance(node.op, ast.Pow) and abs(b) <= 10:
            return a ** b
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in {"float", "int", "abs"} and len(node.args) == 1 and not node.keywords:
        v = _eval(node.args[0], env)
        return {"float": float, "int": int, "abs": abs}[node.func.id](v)
    raise _Unknown


def _constants(tree: ast.Module) -> dict:
    """Module-level names bound once to a constant numeric expression."""
    rebound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.comprehension)):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    rebound.add(n.id)
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            rebound.add(node.target.id)
    env: dict[str, float] = {}
    seen: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            name = stmt.targets[0].id
            if name in seen:
                rebound.add(name)
            seen.add(name)
            try:
                env[name] = _eval(stmt.value, env)
            except _Unknown:
                env.pop(name, None)
    return {k: v for k, v in env.items() if k not in rebound}


def _close(x: float, ref: float, tol: float = REL_TOL) -> bool:
    return abs(x - ref) <= tol * abs(ref)


def _f_calls(node: ast.AST):
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_F":
            yield n


def _strings(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List)):
        out = []
        for e in node.elts:
            s = _strings(e)
            if s is None:
                return None
            out += s
        return out
    return None


def audit_deck(source: str, spec: dict) -> tuple[list[str], list[str]]:
    """Return (problems, names of the loaded cell groups)."""
    tree = ast.parse(source)
    env = _constants(tree)
    problems: list[str] = []
    load_groups: list[str] = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
            continue
        cmd, line = call.func.id, call.lineno
        if cmd == "AFFE_CARA_ELEM":
            problems.append(f"line {line}: {cmd} is not allowed in this model")
        elif cmd == "AFFE_MODELE":
            for f in _f_calls(call):
                for kw in f.keywords:
                    if kw.arg == "MODELISATION" and _strings(kw.value) != ["D_PLAN"]:
                        problems.append(f"line {line}: MODELISATION must be 'D_PLAN' (plane-strain continuum)")
        elif cmd == "DEFI_MATERIAU":
            keys = {kw.arg for kw in call.keywords}
            if keys - {"ELAS"}:
                problems.append(f"line {line}: DEFI_MATERIAU may only define ELAS, not {sorted(keys - {'ELAS'})}")
            for kw in call.keywords:
                if kw.arg != "ELAS":
                    continue
                vals = {k.arg: k.value for f in _f_calls(kw.value) for k in f.keywords}
                try:
                    e, nu = _eval(vals["E"], env), _eval(vals["NU"], env)
                except (KeyError, _Unknown):
                    problems.append(f"line {line}: E and NU must be numeric constants")
                    continue
                if not (_close(e, spec["youngs_modulus"]) and _close(nu, spec["poisson_ratio"])):
                    problems.append(f"line {line}: E and NU do not match the statement")
        elif cmd in {"AFFE_CHAR_MECA", "AFFE_CHAR_MECA_F", "AFFE_CHAR_CINE"}:
            allowed = {"AFFE_CHAR_MECA": CHAR_KEYS,
                       # function-valued boundary tractions (e.g. sigma.n on a curved outer boundary)
                       "AFFE_CHAR_MECA_F": {"MODELE", "INFO", "VERI_NORM"} | LOAD_KEYS,
                       "AFFE_CHAR_CINE": {"MODELE", "MECA_IMPO", "INFO"}}[cmd]
            for kw in call.keywords:
                if kw.arg not in allowed:
                    problems.append(f"line {line}: {cmd} keyword {kw.arg} is not allowed")
                    continue
                if kw.arg in {"DDL_IMPO", "FACE_IMPO", "MECA_IMPO"}:
                    for f in _f_calls(kw.value):
                        for k in f.keywords:
                            if k.arg in DISP_COMPONENTS:
                                try:
                                    ok = _eval(k.value, env) == 0.0
                                except _Unknown:
                                    ok = False
                                if not ok:
                                    problems.append(f"line {line}: imposed {k.arg} must be 0 "
                                                    "(supports and symmetry only)")
                if kw.arg in LOAD_KEYS:
                    for f in _f_calls(kw.value):
                        names = None
                        for k in f.keywords:
                            if k.arg == "GROUP_MA":
                                names = _strings(k.value)
                            elif k.arg in {"TOUT", "MAILLE", "GROUP_NO", "NOEUD"}:
                                problems.append(f"line {line}: {kw.arg} must be applied with GROUP_MA")
                        if names is None:
                            problems.append(f"line {line}: {kw.arg} needs GROUP_MA given as string literals")
                        else:
                            load_groups += names
    return list(dict.fromkeys(problems)), list(dict.fromkeys(load_groups))


def check_load_groups(spec: dict, res: dict) -> list[str]:
    """Loaded groups must lie on the loaded boundary: the inner surface of the cylinder, or the
    outer boundary of the opening model (not the traction-free wall, not inside the mesh)."""
    coords = res["coords"]
    edges: dict[frozenset, int] = {}
    for cell, mtype in zip(res["cells"], res["cell_types"]):
        k = MED_CORNERS.get(mtype)
        if k is None:
            continue
        c = cell[:k]
        for i, j in zip(c, c[1:] + c[:1]):
            e = frozenset((i, j))
            edges[e] = edges.get(e, 0) + 1
    corners = {n for cell, t in zip(res["cells"], res["cell_types"]) if t in MED_CORNERS
               for n in cell[:MED_CORNERS[t]]}
    boundary = {n for e, c in edges.items() if c == 1 for n in e}
    problems = []
    for group, nodes in res["load_groups"].items():
        for n in nodes:
            x, y = coords[n][:2]
            r = math.hypot(x, y)
            if n in corners and n not in boundary:
                problems.append(f"loaded group {group} has nodes inside the mesh")
                break
            if spec["problem"] == "thick-cylinder" and not _close(r, spec["inner_radius"], GEOM_TOL):
                problems.append(f"loaded group {group} is not on the inner (pressurized) surface")
                break
            if spec["problem"] == "circular-hole" and r <= spec["hole_radius"] * (1 + GEOM_TOL):
                problems.append(f"loaded group {group} touches the traction-free opening wall")
                break
    return problems


def check_applied_loads(spec: dict, res: dict) -> list[str]:
    """The assembled load vector must be statically equivalent to the stated traction on the
    loaded boundary of the deck's own mesh (see :mod:`mechgym.loadcheck`)."""
    from .. import loadcheck

    applied = res.get("applied")
    if not applied:
        return ["the applied loads could not be evaluated (FORC_NODA / REAC_NODA)"]
    coords = {i: tuple(c[:2]) for i, c in enumerate(res["coords"])}
    cells, corners = [], []
    for cell, mtype in zip(res["cells"], res["cell_types"]):
        k = MED_CORNERS.get(mtype)
        if k is not None:
            cells.append(list(cell))
            corners.append(k)
    loads = {i: f for i, f in enumerate(applied) if f[0] != 0.0 or f[1] != 0.0}
    out = loadcheck.check_nodal(coords, cells, corners, loads, spec, GEOM_TOL)
    return [] if out["ok"] else [loadcheck.message(spec["problem"], out)]
