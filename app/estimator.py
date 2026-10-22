"""Reads a flat-pattern DXF and works out what the laser has to cut, then prices it.

Geometry: every cuttable entity (lines, arcs, circles, polylines, splines, ellipses) is
turned into a path, flattened finely enough that lengths are accurate to well under 0.1%,
and loose segments are joined end to end into contours. Each contour is one pierce.
The biggest contour that is not inside another one is the outside of a part; anything
inside it is a hole or cutout.

Pricing: cut time = cut length / feed rate + pierces x pierce time, then machine time,
material, setup and handling are added from the rates table, which the shop edits.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field

import ezdxf

CUT_TYPES = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE"}
# Layers that customers use for things the laser should not cut
SKIP_LAYER_WORDS = ("BEND", "ETCH", "MARK", "SCRIBE", "DIM", "TEXT", "NOTE", "TITLE", "BORDER", "CENTER", "CENTRE", "HIDDEN")
INSUNITS_TO_INCH = {1: 1.0, 2: 12.0, 4: 1 / 25.4, 5: 1 / 2.54, 6: 1000 / 25.4}
UNIT_NAMES = {1: "inches", 2: "feet", 4: "millimetres", 5: "centimetres", 6: "metres"}


@dataclass
class Contour:
    points: list[tuple[float, float]]   # in inches
    closed: bool
    length: float = 0.0
    area: float = 0.0                    # unsigned, closed contours only
    role: str = ""                       # outside / hole / open


@dataclass
class PartGeometry:
    name: str
    units_note: str
    contours: list[Contour] = field(default_factory=list)
    cut_length: float = 0.0              # inches
    pierces: int = 0
    holes: int = 0
    parts_in_file: int = 0
    width: float = 0.0
    height: float = 0.0
    net_area: float = 0.0                # sq in, outsides minus holes
    smallest_hole: float | None = None   # diameter of the smallest round hole, inches
    entities_used: int = 0
    skipped_layers: list[str] = field(default_factory=list)
    duplicates_removed: int = 0
    dashed_skipped: int = 0
    gaps_closed: int = 0
    warnings: list[str] = field(default_factory=list)


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _poly_length(pts):
    return sum(_dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def _poly_area(pts):
    s = 0.0
    for i in range(len(pts) - 1):
        s += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
    return abs(s) / 2


def _inside(pt, poly):
    x, y = pt
    hit = False
    for i in range(len(poly) - 1):
        (x1, y1), (x2, y2) = poly[i], poly[i + 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def _pieces(e, scale):
    """Yields (points, exact length) for one entity, in inches. Lines, arcs and circles
    are measured exactly; polylines are split into their real lines and arcs; splines and
    ellipses are measured along very finely spaced points on the true curve."""
    kind = e.dxftype()
    if kind in ("LWPOLYLINE", "POLYLINE"):
        for sub in e.virtual_entities():
            yield from _pieces(sub, scale)
        return
    if kind == "LINE":
        a, b = e.dxf.start, e.dxf.end
        pts = [(a.x, a.y), (b.x, b.y)]
        length = math.dist(pts[0], pts[1])
    elif kind == "ARC":
        span = (e.dxf.end_angle - e.dxf.start_angle) % 360 or 360
        pts = [(v.x, v.y) for v in e.flattening(sagitta=0.0005)]
        length = e.dxf.radius * math.radians(span)
    elif kind == "CIRCLE":
        pts = [(v.x, v.y) for v in e.flattening(sagitta=0.0005)]
        length = 2 * math.pi * e.dxf.radius
    else:  # SPLINE, ELLIPSE
        pts = [(v.x, v.y) for v in e.flattening(distance=0.0001)]
        length = _poly_length(pts)
    if len(pts) >= 2 and length > 1e-9:
        yield [(x * scale, y * scale) for x, y in pts], length * scale


def _n(k, word):
    return f"{k} {word}" + ("" if k == 1 else "s")


def _skip_layer(layer: str) -> bool:
    up = layer.upper()
    return any(w in up for w in SKIP_LAYER_WORDS)


def read_dxf(data: bytes, name: str, units_override: str = "auto", join_tol_in: float = 0.002) -> PartGeometry:
    # recover.read copes with Windows line endings, old code pages, binary DXF and
    # the small structural faults that customer files often have
    from ezdxf import recover
    doc, _ = recover.read(io.BytesIO(data))
    msp = doc.modelspace()
    code = doc.header.get("$INSUNITS", 0)
    if units_override == "inches":
        scale, units_note = 1.0, "inches (set by you)"
    elif units_override == "millimetres":
        scale, units_note = 1 / 25.4, "millimetres (set by you)"
    elif code in INSUNITS_TO_INCH:
        scale, units_note = INSUNITS_TO_INCH[code], f"{UNIT_NAMES[code]} (from the file)"
    else:
        scale, units_note = 1.0, "inches (the file doesn't say, so inches were assumed)"

    geo = PartGeometry(name=name, units_note=units_note)
    pieces: list[list[tuple[float, float]]] = []
    skipped = set()
    for e in msp:
        if e.dxftype() not in CUT_TYPES:
            continue
        layer = e.dxf.get("layer", "0")
        if _skip_layer(layer):
            skipped.add(layer)
            continue
        # SolidWorks, Inventor and others export bend lines on the cut layer but dashed
        # (HIDDEN for bend down, CENTER for bend up), so a dashed line is never a cut
        lt = e.dxf.get("linetype", "BYLAYER").upper()
        if lt == "BYLAYER":
            lt = doc.layers.get(layer).dxf.linetype.upper() if doc.layers.has_entry(layer) else "CONTINUOUS"
        if lt not in ("CONTINUOUS", "BYBLOCK"):
            geo.dashed_skipped += 1
            continue
        geo.entities_used += 1
        pieces.extend(_pieces(e, scale))
    geo.skipped_layers = sorted(skipped)

    # Customer files often have the same line drawn twice; the laser would cut it twice
    seen, unique = set(), []
    for pts, length in pieces:
        mid = pts[len(pts) // 2]
        key = tuple(sorted([tuple(round(c, 4) for c in pts[0]), tuple(round(c, 4) for c in pts[-1])])) + (round(length, 4), round(mid[0], 3), round(mid[1], 3))
        if key in seen:
            geo.duplicates_removed += 1
            continue
        seen.add(key)
        unique.append((pts, length))

    geo.contours = _chain(unique, join_tol_in, geo)
    _classify(geo)
    return geo


def _chain(pieces, tol, geo):
    contours, open_pieces = [], []
    for pts, length in pieces:
        if _dist(pts[0], pts[-1]) <= tol and len(pts) > 2:
            contours.append(Contour(points=pts[:-1] + [pts[0]], closed=True, length=length))
        else:
            open_pieces.append((list(pts), length))
    # join loose segments end to end, nearest match first
    while open_pieces:
        chain, chain_len = open_pieces.pop(0)
        grown = True
        while grown:
            grown = False
            if _dist(chain[0], chain[-1]) <= tol and len(chain) > 2:
                break
            best = None
            for i, (p, plen) in enumerate(open_pieces):
                for end_at_tail in (True, False):
                    anchor = chain[-1] if end_at_tail else chain[0]
                    for rev in (False, True):
                        q = p[::-1] if rev else p
                        d = _dist(anchor, q[0] if end_at_tail else q[-1])
                        if d <= tol and (best is None or d < best[0]):
                            best = (d, i, end_at_tail, q, plen)
            if best:
                d, i, end_at_tail, q, plen = best
                open_pieces.pop(i)
                if d > 1e-6:
                    geo.gaps_closed += 1
                chain = chain + q[1:] if end_at_tail else q[:-1] + chain
                chain_len += plen
                grown = True
        closed = _dist(chain[0], chain[-1]) <= tol and len(chain) > 2
        if closed:
            if _dist(chain[0], chain[-1]) > 1e-6:
                geo.gaps_closed += 1
            chain = chain[:-1] + [chain[0]]
        contours.append(Contour(points=chain, closed=closed, length=chain_len))
    for c in contours:
        c.area = _poly_area(c.points) if c.closed else 0.0
    return contours


def _classify(geo: PartGeometry):
    closed = sorted([c for c in geo.contours if c.closed], key=lambda c: -c.area)
    for i, c in enumerate(closed):
        depth = sum(1 for o in closed[:i] if o.area > c.area and _inside(c.points[0], o.points))
        c.role = "outside" if depth % 2 == 0 else "hole"
    for c in geo.contours:
        if not c.closed:
            c.role = "open"
    geo.cut_length = sum(c.length for c in geo.contours)
    geo.pierces = len(geo.contours)
    geo.holes = sum(1 for c in geo.contours if c.role == "hole")
    geo.parts_in_file = sum(1 for c in geo.contours if c.role == "outside")
    geo.net_area = sum(c.area for c in geo.contours if c.role == "outside") - sum(c.area for c in geo.contours if c.role == "hole")
    allpts = [p for c in geo.contours for p in c.points]
    if allpts:
        xs, ys = [p[0] for p in allpts], [p[1] for p in allpts]
        geo.width, geo.height = max(xs) - min(xs), max(ys) - min(ys)
    # a hole counts as round when its area matches a circle of the same perimeter
    round_holes = [c for c in geo.contours if c.role == "hole" and c.length > 0 and abs(4 * math.pi * c.area / c.length ** 2 - 1) < 0.01]
    if round_holes:
        geo.smallest_hole = min(c.length / math.pi for c in round_holes)

    open_n = sum(1 for c in geo.contours if c.role == "open")
    if not geo.contours:
        geo.warnings.append("No cuttable geometry found. Check the file isn't a block or only text.")
    if open_n:
        geo.warnings.append(f"{_n(open_n, 'open contour')}: the outline has a gap or there is a stray line. Each one is still counted as a cut and a pierce.")
    if geo.duplicates_removed:
        geo.warnings.append(f"{_n(geo.duplicates_removed, 'duplicate line')} removed so the laser doesn't cut the same path twice.")
    if geo.gaps_closed:
        geo.warnings.append(f"{_n(geo.gaps_closed, 'tiny gap')} closed when joining the outline.")
    if geo.parts_in_file > 1:
        geo.warnings.append(f"This file holds {geo.parts_in_file} separate parts. Totals cover all of them.")
    if geo.dashed_skipped:
        geo.warnings.append(f"{_n(geo.dashed_skipped, 'dashed line')} left out as bend or centre lines.")
    if geo.skipped_layers:
        geo.warnings.append("Left out because the layer name says bend, etch or notes: " + ", ".join(geo.skipped_layers))


def estimate(geo: PartGeometry, rate: dict, qty: int, settings: dict) -> dict:
    """rate: one row of the rates table. settings: shop-wide charges."""
    feed = float(rate["feed_ipm"])
    cut_min = geo.cut_length / feed if feed > 0 else 0.0
    pierce_min = geo.pierces * float(rate["pierce_sec"]) / 60
    run_min_each = (cut_min + pierce_min) * (1 + settings["motion_allowance_pct"] / 100)
    machine_each = run_min_each / 60 * settings["machine_rate_hr"]

    t = float(rate["thickness_in"])
    if settings["material_basis"] == "Rectangle the part needs":
        mat_area = (geo.width + settings["part_spacing_in"]) * (geo.height + settings["part_spacing_in"])
    else:
        mat_area = geo.net_area
    weight_each = geo.net_area * t * float(rate["density_lb_in3"])
    material_each = mat_area * t * float(rate["density_lb_in3"]) * float(rate["price_per_lb"]) * (1 + settings["material_markup_pct"] / 100)

    each = machine_each + material_each + settings["handling_per_part"]
    subtotal = each * qty + settings["setup_per_job"]
    total = max(subtotal, settings["minimum_charge"])
    warnings = []
    if geo.smallest_hole is not None and geo.smallest_hole < t:
        warnings.append(f"Smallest hole ({geo.smallest_hole:.3f} in) is smaller than the material thickness ({t:.3f} in). It may need drilling or a special cut.")
    return {
        "cut_min_each": cut_min, "pierce_min_each": pierce_min, "run_min_each": run_min_each,
        "machine_each": machine_each, "material_each": material_each, "weight_each": weight_each,
        "each": each, "qty": qty, "setup": settings["setup_per_job"], "subtotal": subtotal,
        "total": total, "minimum_applied": total > subtotal, "warnings": warnings,
    }


def job_total(lines: list[dict], settings: dict) -> dict:
    """Setup is charged once per job and the minimum charge applies to the whole job,
    not to each part."""
    parts = sum(l["each"] * l["qty"] for l in lines)
    subtotal = parts + settings["setup_per_job"]
    total = max(subtotal, settings["minimum_charge"]) if lines else 0.0
    return {"parts": parts, "setup": settings["setup_per_job"], "subtotal": subtotal,
            "total": total, "minimum_applied": bool(lines) and total > subtotal,
            "run_min": sum(l["run_min_each"] * l["qty"] for l in lines)}


def svg_preview(geo: PartGeometry, size: int = 360) -> str:
    if not geo.contours:
        return ""
    allpts = [p for c in geo.contours for p in c.points]
    minx, miny = min(p[0] for p in allpts), min(p[1] for p in allpts)
    w, h = max(geo.width, 1e-6), max(geo.height, 1e-6)
    pad = max(w, h) * 0.04
    vb = f"{minx - pad:.4f} {-(miny + h) - pad:.4f} {w + 2 * pad:.4f} {h + 2 * pad:.4f}"
    stroke = max(w, h) / 320
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" width="100%" style="max-height:{size}px" role="img" aria-label="Preview of {geo.name}">']
    colours = {"outside": "#1d2327", "hole": "#1d2327", "open": "#c2410c"}
    for c in geo.contours:
        d = " ".join(f"{'M' if i == 0 else 'L'}{x:.4f},{-y:.4f}" for i, (x, y) in enumerate(c.points))
        dash = f' stroke-dasharray="{stroke * 4:.4f} {stroke * 3:.4f}"' if c.role == "open" else ""
        out.append(f'<path d="{d}" fill="none" stroke="{colours[c.role]}" stroke-width="{stroke:.4f}"{dash}/>')
        x, y = c.points[0]
        out.append(f'<circle cx="{x:.4f}" cy="{-y:.4f}" r="{stroke * 2.2:.4f}" fill="#e8590c"/>')
    out.append("</svg>")
    return "".join(out)
