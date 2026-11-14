"""Boundary faces of an ASCII OpenFOAM polyMesh and the loads a solidDisplacementFoam case applies.

Used by the verifier to check that the traction boundary conditions are statically equivalent to
the stated far-field stress (:mod:`mechgym.loadcheck`). The verifier forces ``writeFormat ascii``
in its own copy of the case, so the mesh files are always ASCII here.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .. import loadcheck
from .audit import _entries, _keyword
from .reader import _block, _strip_comments

_VEC = re.compile(r"\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)")
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
NON_LOADED = {"empty", "symmetryPlane", "symmetry", "wedge"}


def _body(path: Path) -> str:
    text = _strip_comments(path.read_text(errors="replace"))
    m = re.search(r"FoamFile\s*\{", text)
    if m:
        i, depth = m.end(), 1
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        text = text[i:]
    return text


def read_points(mesh: Path) -> np.ndarray:
    body = _body(mesh / "points")
    head = re.search(r"(\d+)\s*\(", body)
    pts = np.array([[float(g) for g in v.groups()] for v in _VEC.finditer(body[head.end():])])
    return pts[:int(head.group(1))]


def read_faces(mesh: Path) -> list[list[int]]:
    body = _body(mesh / "faces")
    head = re.search(r"(\d+)\s*\(", body)
    faces = [[int(v) for v in m.group(2).split()]
             for m in re.finditer(r"(\d+)\s*\(([\d\s]+)\)", body[head.end():])]
    return faces[:int(head.group(1))]


def read_boundary(mesh: Path) -> dict[str, dict]:
    body = _body(mesh / "boundary")
    out = {}
    for name, entry in _entries(body).items():
        out[name] = {"type": _keyword(entry, "type"), "nFaces": int(_keyword(entry, "nFaces")),
                     "startFace": int(_keyword(entry, "startFace"))}
    return out


def _field(entry: str, key: str, n: int, width: int) -> np.ndarray:
    m = re.search(r"(?:^|[\s;{])" + key + r"\s+(uniform|nonuniform)\s+", entry)
    if not m:
        raise ValueError(f"no {key}")
    rest = entry[m.end():]
    if m.group(1) == "uniform":
        if width == 3:
            v = _VEC.match(rest.strip())
            return np.repeat(np.array([[float(v.group(k)) for k in (1, 2, 3)]]), n, axis=0)
        return np.full(n, float(re.match(_NUM, rest.strip()).group(0)))
    head = re.match(r"List<\w+>\s*(\d+)\s*\(", rest)
    if not head:
        raise ValueError(f"unexpected {key} list format")
    count = int(head.group(1))
    if width == 3:
        vals = np.array([[float(g) for g in v.groups()] for v in _VEC.finditer(rest[head.end():])][:count])
    else:
        vals = np.array([float(x) for x in re.findall(_NUM, rest[head.end():rest.index(")", head.end())])])
    if len(vals) != count or count != n:
        raise ValueError(f"{key}: expected {n} values, got {len(vals)}")
    return vals


def check_case_loads(work: Path, time_dir: Path, spec: dict) -> dict:
    """Moments of the applied boundary forces vs the stated far-field traction on the loaded
    boundary (every patch except ``hole`` and symmetry/empty patches)."""
    mesh = work / "constant/polyMesh"
    pts, faces, bnd = read_points(mesh), read_faces(mesh), read_boundary(mesh)
    d_text = _strip_comments((time_dir / "D").read_text(errors="replace"))
    bcs = _entries(_block(d_text, "boundaryField"))
    sigma = loadcheck.stated_stress(spec)
    exp_pts, exp_t = [], []
    app_pts, app_f = [], []
    for name, info in bnd.items():
        if name == "hole" or info["type"] in NON_LOADED:
            continue
        idx = range(info["startFace"], info["startFace"] + info["nFaces"])
        cent, area = [], []
        for k in idx:
            p = pts[faces[k]]
            cent.append(p.mean(axis=0))
            area.append(0.5 * sum(np.cross(p[i], p[(i + 1) % len(p)]) for i in range(len(p))))
        cent, area = np.array(cent), np.array(area)
        in_plane = np.abs(area[:, 2]) <= 0.5 * np.linalg.norm(area, axis=1)
        cent, area = cent[in_plane, :2], area[in_plane, :2]
        if not len(cent):
            continue
        exp_pts.append(cent)
        exp_t.append(area @ sigma.T)                     # sigma . S_f
        entry = bcs.get(name, "")
        if _keyword(entry, "type") == "tractionDisplacement":
            n = info["nFaces"]
            t = _field(entry, "traction", n, 3)[in_plane][:, :2]
            p = _field(entry, "pressure", n, 1)[in_plane]
            mag = np.linalg.norm(area, axis=1)
            app_pts.append(cent)
            app_f.append(t * mag[:, None] - p[:, None] * area)
    if not exp_pts:
        return {"linear_error": float("inf"), "quadratic_error": float("inf"), "ok": False}
    ep, et = np.vstack(exp_pts), np.vstack(exp_t)
    scale = max(float(np.abs(ep).max()), 1e-300)
    expected = loadcheck.applied_moments(ep, et, scale)
    applied = loadcheck.applied_moments(np.vstack(app_pts), np.vstack(app_f), scale) if app_pts \
        else np.zeros(12)
    return loadcheck.compare(applied, expected)
