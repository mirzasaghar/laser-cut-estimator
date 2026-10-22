"""Reads a STEP model of a sheet-metal part and measures what the laser would cut.

A part can be quoted straight from STEP when it is a flat plate: every face is either one
of the two big faces (top and bottom, parallel to each other) or a side wall standing at
right angles to them. Then the top face's outline and holes are exactly the cut path.
A part with bends, bevels or countersinks fails that test and is flagged instead of
priced, because it needs a flat pattern first.

Plain Python on purpose, so the same code runs on a shop PC and in the browser demo.
Results are checked against OpenCascade in tests/step_check.py.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- STEP file parsing

_ENTITY = re.compile(r"#(\d+)\s*=\s*(.*?);\s*(?=#\d+\s*=|ENDSEC)", re.S)


def _tokens(s):
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace() or c == ",":
            i += 1
        elif c in "()":
            yield c
            i += 1
        elif c == "'":
            j = i + 1
            while j < n:
                if s[j] == "'" and (j + 1 >= n or s[j + 1] != "'"):
                    break
                j += 2 if s[j] == "'" else 1
            yield ("str", s[i + 1:j])
            i = j + 1
        elif c == "#":
            j = i + 1
            while j < n and s[j].isdigit():
                j += 1
            yield ("ref", int(s[i + 1:j]))
            i = j
        elif c == ".":
            j = s.index(".", i + 1)
            yield ("enum", s[i + 1:j])
            i = j + 1
        elif c in "$*":
            yield ("null", None)
            i += 1
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            yield ("name", s[i:j])
            i = j
        else:
            j = i
            while j < n and s[j] not in " ,()\t\r\n":
                j += 1
            try:
                yield ("num", float(s[i:j]))
            except ValueError:
                yield ("str", s[i:j])
            i = j


def _parse_list(toks):
    out = []
    for t in toks:
        if t == ")":
            return out
        if t == "(":
            out.append(_parse_list(toks))
        elif t[0] == "name":
            # a typed value such as LENGTH_MEASURE(1.5) or a part of a complex entity
            nxt = next(toks)
            out.append((t[1], _parse_list(toks)) if nxt == "(" else t[1])
        else:
            out.append(t[1])
    return out


def parse_step(text: str) -> dict[int, tuple[str, list]]:
    data = text[text.index("DATA;") + 5:] if "DATA;" in text else text
    ents = {}
    for m in _ENTITY.finditer(data + "\n#0=X;"):
        num, body = int(m.group(1)), m.group(2).strip()
        toks = _tokens(body)
        first = next(toks)
        if first == "(":  # complex entity: ( A(...) B(...) )
            parts = _parse_list(toks)
            ents[num] = ("COMPLEX", parts)
        elif first[0] == "name":
            next(toks)  # "("
            ents[num] = (first[1], _parse_list(toks))
    return ents


# ---------------------------------------------------------------- small vector helpers

def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _mul(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def _norm(a): return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n else a


# ---------------------------------------------------------------- geometry model

@dataclass
class StepPart:
    name: str
    units_note: str = ""
    flat: bool = False
    reason: str = ""                 # why it can't be priced from STEP, if not flat
    thickness: float = 0.0           # inches
    loops: list[list[tuple[float, float]]] = field(default_factory=list)   # top-face loops in plate coordinates, inches
    loop_lengths: list[float] = field(default_factory=list)                  # exact edge lengths per loop, inches
    solids: int = 0


class _Model:
    def __init__(self, ents):
        self.e = ents

    def get(self, ref):
        return self.e[ref]

    def point(self, ref):
        t, a = self.e[ref]
        if t == "VERTEX_POINT":
            return self.point(a[1])
        xyz = a[1]
        return (float(xyz[0]), float(xyz[1]), float(xyz[2]) if len(xyz) > 2 else 0.0)

    def direction(self, ref):
        t, a = self.e[ref]
        r = a[1]
        return _unit((float(r[0]), float(r[1]), float(r[2]) if len(r) > 2 else 0.0))

    def placement(self, ref):
        _, a = self.e[ref]
        origin = self.point(a[1])
        z = self.direction(a[2]) if a[2] is not None else (0.0, 0.0, 1.0)
        x = self.direction(a[3]) if len(a) > 3 and a[3] is not None else None
        if x is None or abs(_dot(x, z)) > 0.999:
            x = _unit(_cross((0, 1, 0) if abs(z[1]) < 0.9 else (1, 0, 0), z))
        x = _unit(_sub(x, _mul(z, _dot(x, z))))
        return origin, z, x, _cross(z, x)

    def curve(self, ref):
        """Returns (kind, data) for the 3D curve of an edge, unwrapping SURFACE_CURVE etc."""
        t, a = self.e[ref]
        if t in ("SURFACE_CURVE", "SEAM_CURVE", "INTERSECTION_CURVE"):
            return self.curve(a[1])
        if t == "TRIMMED_CURVE":
            return self.curve(a[1])
        return t, a, ref

    def complex_parts(self, a):
        return {p[0]: p[1] for p in a if isinstance(p, tuple)}


def _bspline_points(model, kind, a, ref, samples=200):
    """Evaluates a (rational) B-spline curve at evenly spaced parameters."""
    if kind == "COMPLEX":
        parts = model.complex_parts(a)
        bs, kn = parts.get("B_SPLINE_CURVE"), parts.get("B_SPLINE_CURVE_WITH_KNOTS")
        degree, ctrl = int(bs[0]), bs[1]
        mults, knots_u = kn[0], kn[1]
        weights = parts.get("RATIONAL_B_SPLINE_CURVE", [[1.0] * len(ctrl)])[0]
    else:
        degree, ctrl = int(a[1]), a[2]
        mults, knots_u = a[6], a[7]
        weights = [1.0] * len(ctrl)
    pts = [model.point(r) for r in ctrl]
    knots = [float(k) for k, m in zip(knots_u, mults) for _ in range(int(m))]
    lo, hi = knots[degree], knots[-degree - 1]

    def eval_at(u):
        k = degree
        for i in range(degree, len(knots) - degree - 1):
            if knots[i] <= u < knots[i + 1]:
                k = i
                break
        else:
            k = len(knots) - degree - 2
        d = [(*_mul(pts[j], float(weights[j])), float(weights[j])) for j in range(k - degree, k + 1)]
        for r in range(1, degree + 1):
            for j in range(degree, r - 1, -1):
                i = j + k - degree
                den = knots[i + degree - r + 1] - knots[i]
                alpha = 0.0 if den == 0 else (u - knots[i]) / den
                d[j] = tuple((1 - alpha) * d[j - 1][c] + alpha * d[j][c] for c in range(4))
        w = d[degree][3]
        return (d[degree][0] / w, d[degree][1] / w, d[degree][2] / w)

    return [eval_at(lo + (hi - lo) * i / samples) for i in range(samples)] + [eval_at(hi - 1e-12) if hi > lo else pts[-1]]


def _edge_points(model, edge_ref, forward=True):
    """3D points along an edge from its start vertex to its end vertex, plus its length."""
    _, ea = model.get(edge_ref)
    v1, v2 = model.point(ea[1]), model.point(ea[2])
    kind, ca, cref = model.curve(ea[3])
    same_sense = ea[4] in ("T", True)
    if kind == "LINE":
        pts = [v1, v2]
        length = _norm(_sub(v2, v1))
    elif kind in ("CIRCLE", "ELLIPSE"):
        o, z, x, y = model.placement(ca[1])
        r1 = float(ca[2])
        r2 = float(ca[3]) if kind == "ELLIPSE" else r1

        def ang(p):
            d = _sub(p, o)
            return math.atan2(_dot(d, y) / r2, _dot(d, x) / r1)
        a1, a2 = ang(v1), ang(v2)
        if same_sense:
            span = (a2 - a1) % (2 * math.pi)
        else:
            span = -((a1 - a2) % (2 * math.pi))
        if abs(span) < 1e-9:   # start and end on the same point: a full circle
            span = 2 * math.pi if same_sense else -2 * math.pi
        steps = max(8, int(abs(span) / (2 * math.pi) * 180))
        pts = [_add(o, _add(_mul(x, r1 * math.cos(a1 + span * i / steps)), _mul(y, r2 * math.sin(a1 + span * i / steps)))) for i in range(steps + 1)]
        if kind == "CIRCLE":
            length = r1 * abs(span)
        else:
            length = sum(_norm(_sub(pts[i + 1], pts[i])) for i in range(len(pts) - 1))
    elif kind in ("B_SPLINE_CURVE_WITH_KNOTS", "COMPLEX"):
        pts = _bspline_points(model, kind, ca, cref, 400)
        if not same_sense:
            pts = pts[::-1]
        # trim to the edge's own vertices (the curve can run past them)
        i1 = min(range(len(pts)), key=lambda i: _norm(_sub(pts[i], v1)))
        i2 = min(range(len(pts)), key=lambda i: _norm(_sub(pts[i], v2)))
        if i2 > i1:
            pts = [v1] + pts[i1 + 1:i2] + [v2]
        elif _norm(_sub(v1, v2)) < 1e-9:
            pts = [v1] + pts[i1 + 1:] + pts[:i1] + [v2]
        else:
            pts = [v1, v2]
        length = sum(_norm(_sub(pts[i + 1], pts[i])) for i in range(len(pts) - 1))
    else:
        pts = [v1, v2]
        length = _norm(_sub(v2, v1))
    if not forward:
        pts = pts[::-1]
    return pts, length


def _length_scale(ents) -> tuple[float, str]:
    for t, a in ents.values():
        if t != "COMPLEX":
            continue
        parts = {p[0]: p[1] for p in a if isinstance(p, tuple)}
        if "LENGTH_UNIT" in parts:
            if "SI_UNIT" in parts:
                prefix = parts["SI_UNIT"][0]
                return {"MILLI": 1 / 25.4, "CENTI": 1 / 2.54, None: 39.3700787}.get(prefix, 1 / 25.4), \
                    {"MILLI": "millimetres", "CENTI": "centimetres", None: "metres"}.get(prefix, "millimetres")
            if "CONVERSION_BASED_UNIT" in parts:
                name = str(parts["CONVERSION_BASED_UNIT"][0]).upper()
                if "INCH" in name:
                    return 1.0, "inches"
                if "FOOT" in name:
                    return 12.0, "feet"
    return 1 / 25.4, "millimetres (assumed)"


def _face_loops(model, face_ref):
    _, fa = model.get(face_ref)
    loops = []
    for bref in fa[1]:
        bt, ba = model.get(bref)
        lt, la = model.get(ba[1])
        if lt != "EDGE_LOOP":
            continue
        pts, length = [], 0.0
        for oref in la[1]:
            _, oa = model.get(oref)
            forward = oa[4] in ("T", True)
            epts, elen = _edge_points(model, oa[3], forward)
            pts.extend(epts if not pts else epts[1:])
            length += elen
        loops.append((pts, length, bt == "FACE_OUTER_BOUND"))
    return loops


def _area_2d(pts):
    s = 0.0
    for i in range(len(pts) - 1):
        s += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
    return abs(s) / 2


def read_step(data: bytes, name: str) -> StepPart:
    text = data.decode("latin-1")
    ents = parse_step(text)
    model = _Model(ents)
    scale, units = _length_scale(ents)
    part = StepPart(name=name, units_note=f"{units} (from the file)")
    faces = [k for k, (t, _) in ents.items() if t == "ADVANCED_FACE"]
    part.solids = sum(1 for t, _ in ents.values() if t == "MANIFOLD_SOLID_BREP")
    if not faces:
        part.reason = "No solid model found in the file."
        return part

    planes = []
    for f in faces:
        st, sa = model.get(model.get(f)[1][2])
        if st == "PLANE":
            o, z, x, y = model.placement(sa[1])
            loops = _face_loops(model, f)
            outer = max(loops, key=lambda L: _area_2d([(_dot(_sub(p, o), x), _dot(_sub(p, o), y)) for p in L[0]]), default=None)
            area = 0.0
            for L in loops:
                a2 = _area_2d([(_dot(_sub(p, o), x), _dot(_sub(p, o), y)) for p in L[0]])
                area += a2 if L is outer else -a2
            planes.append({"face": f, "o": o, "z": z, "x": x, "y": y, "area": area, "loops": loops})
    if not planes:
        part.reason = "The model has no flat faces, so it isn't a sheet part."
        return part
    top = max(planes, key=lambda p: p["area"])
    n = top["z"]

    caps, problems = set(), []
    for f in faces:
        st, sa = model.get(model.get(f)[1][2])
        if st == "PLANE":
            o, z, *_ = model.placement(sa[1])
            c = abs(_dot(z, n))
            if c > 0.9999:
                caps.add(round(_dot(o, n) * scale, 4))
            elif c > 1e-4:
                problems.append("a face at an angle (bevel, chamfer or bend)")
        elif st in ("CYLINDRICAL_SURFACE", "SURFACE_OF_LINEAR_EXTRUSION"):
            if st == "CYLINDRICAL_SURFACE":
                axis = model.placement(sa[1])[1]
            else:
                vt, va = model.get(sa[2])
                axis = model.direction(va[1]) if vt == "VECTOR" else n
            if abs(abs(_dot(axis, n)) - 1) > 1e-4:
                problems.append("a curved face across the plate (a bend)")
        elif st in ("CONICAL_SURFACE",):
            problems.append("a cone (countersink)")
        elif st in ("TOROIDAL_SURFACE", "SPHERICAL_SURFACE"):
            problems.append("a rounded edge or formed feature")
        else:
            problems.append("a free-form surface")
    if len(caps) > 2:
        problems.append("faces at more than two heights (steps, pockets or a bend)")

    if part.solids > 1:
        problems.append(f"{part.solids} separate solids")
    if problems:
        uniq = list(dict.fromkeys(problems))
        part.reason = "Not a flat plate: it has " + ", ".join(uniq[:3]) + ". It needs a flat pattern (DXF) before it can be priced."
        return part

    part.flat = True
    part.thickness = (max(caps) - min(caps)) if len(caps) == 2 else 0.0
    o, x, y = top["o"], top["x"], top["y"]
    for pts, length, _ in top["loops"]:
        part.loops.append([(_dot(_sub(p, o), x) * scale, _dot(_sub(p, o), y) * scale) for p in pts])
        part.loop_lengths.append(length * scale)
    return part
