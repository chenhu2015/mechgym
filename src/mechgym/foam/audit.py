"""Admissibility audit of an OpenFOAM solidDisplacementFoam case: is it the stated problem?

``check_case`` keeps dictionaries from running code. This audit closes the route of injecting the
answer through the case itself: a ``fixedValue`` displacement on the opening wall, an initial
displacement field set to the closed-form solution (with too few iterations to move away from it),
a tuned material, or thermal loads. Rules:

- ``0/D``: ``internalField uniform (0 0 0)``; an explicit ``hole`` entry of type
  ``tractionDisplacement`` with ``traction uniform (0 0 0)`` and ``pressure uniform 0``; every other
  patch ``tractionDisplacement``, ``symmetryPlane``, ``symmetry``, ``empty``, ``zeroGradient``, or
  ``fixedValue`` with ``value uniform (0 0 0)``;
- ``constant/mechanicalProperties``: the stated ``E`` and ``nu`` (within 0.5%), ``planeStress no``;
- ``constant/thermalProperties``: ``thermalStress no``.
"""

from __future__ import annotations

import re
from pathlib import Path

from .reader import _block, _strip_comments

REL_TOL = 0.005
PATCH_TYPES = {"tractionDisplacement", "symmetryPlane", "symmetry", "empty", "zeroGradient", "fixedValue"}
_ZERO_VEC = re.compile(r"^uniform\s*\(\s*[-+]?0*\.?0*(e[-+]?\d+)?\s+[-+]?0*\.?0*(e[-+]?\d+)?\s+"
                       r"[-+]?0*\.?0*(e[-+]?\d+)?\s*\)$", re.I)
_ZERO_SCALAR = re.compile(r"^uniform\s+[-+]?0*\.?0*(e[-+]?\d+)?$", re.I)


def _entries(body: str) -> dict[str, str]:
    """Top-level ``name { ... }`` entries of a dictionary body (names may be quoted)."""
    out, i = {}, 0
    pat = re.compile(r'\s*("[^"]*"|[A-Za-z_][\w.:-]*)\s*\{')
    while True:
        m = pat.search(body, i)
        if not m:
            return out
        j, depth = m.end(), 1
        while depth and j < len(body):
            depth += {"{": 1, "}": -1}.get(body[j], 0)
            j += 1
        out[m.group(1).strip('"')] = body[m.end():j - 1]
        i = j


def _keyword(body: str, key: str) -> str | None:
    m = re.search(r"(?:^|[\s;{])" + re.escape(key) + r"\s+([^;]*);", body)
    return m.group(1).strip() if m else None


def _scalar(text: str, name: str) -> float | None:
    """Value of a material constant: ``name { type uniform; value X; }`` or ``name ... X;``."""
    try:
        v = _keyword(_block(text, name), "value")
        return float(v) if v is not None else None
    except (KeyError, ValueError):
        pass
    m = re.search(r"(?m)^\s*" + re.escape(name) + r"\s+[^;{]*?([-+]?[0-9.]+(?:[eE][-+]?\d+)?)\s*;", text)
    return float(m.group(1)) if m else None


def audit_case(case: Path, spec: dict) -> list[str]:
    problems: list[str] = []
    d_text = _strip_comments((case / "0/D").read_text(errors="replace"))
    internal = _keyword(d_text, "internalField")
    if internal is None or not _ZERO_VEC.match(internal):
        problems.append("0/D: internalField must be uniform (0 0 0)")
    try:
        patches = _entries(_block(d_text, "boundaryField"))
    except KeyError:
        return problems + ["0/D: no boundaryField"]
    if "hole" not in patches:
        problems.append("0/D: the 'hole' patch needs its own entry")
    for name, body in patches.items():
        ptype = _keyword(body, "type")
        if name == "hole":
            traction, pressure = _keyword(body, "traction"), _keyword(body, "pressure")
            if ptype != "tractionDisplacement" or not (traction and _ZERO_VEC.match(traction)) \
                    or not (pressure and _ZERO_SCALAR.match(pressure)):
                problems.append("0/D: the opening wall 'hole' must be traction-free "
                                "(tractionDisplacement, traction uniform (0 0 0), pressure uniform 0)")
        elif ptype not in PATCH_TYPES:
            problems.append(f"0/D: patch {name} has type {ptype}, which is not allowed")
        elif ptype == "fixedValue" and not _ZERO_VEC.match(_keyword(body, "value") or ""):
            problems.append(f"0/D: fixedValue on patch {name} must be uniform (0 0 0)")

    mech = _strip_comments((case / "constant/mechanicalProperties").read_text(errors="replace"))
    e, nu = _scalar(mech, "E"), _scalar(mech, "nu")
    if e is None or nu is None or abs(e - spec["youngs_modulus"]) > REL_TOL * spec["youngs_modulus"] \
            or abs(nu - spec["poisson_ratio"]) > REL_TOL * spec["poisson_ratio"]:
        problems.append("constant/mechanicalProperties: E and nu must be the stated values")
    if _keyword(mech, "planeStress") not in {"no", "off", "false"}:
        problems.append("constant/mechanicalProperties: planeStress must be no (plane strain)")
    thermal = case / "constant/thermalProperties"
    if thermal.exists():
        t_text = _strip_comments(thermal.read_text(errors="replace"))
        if _keyword(t_text, "thermalStress") not in {"no", "off", "false"}:
            problems.append("constant/thermalProperties: thermalStress must be no")
    return problems
