"""The calls the screen makes. Everything goes in and out as plain JSON-ready values, so the
same functions serve the browser demo (Pyodide) and the shop PC (server.py)."""
import base64
from pathlib import Path

import estimator
import quote_doc

HERE = Path(__file__).parent
_parts: dict[str, estimator.PartGeometry] = {}


def _thin(points, limit=600):
    """Drawing only: long polylines are thinned so the browser draws them quickly."""
    if len(points) <= limit:
        return [[round(x, 4), round(y, 4)] for x, y in points]
    step = len(points) / limit
    keep = [points[int(i * step)] for i in range(limit)] + [points[-1]]
    return [[round(x, 4), round(y, 4)] for x, y in keep]


def default_rates() -> dict:
    rows, charges = estimator.load_rates_workbook((HERE / "rates.xlsx").read_bytes())
    return {"rows": rows, "charges": charges}


def read_rates(b64: str) -> dict:
    rows, charges = estimator.load_rates_workbook(base64.b64decode(b64))
    return {"rows": rows, "charges": charges}


SAMPLES = ["l-bracket-flat.dxf", "flange-print.pdf", "mounting-plate-3d.step", "l-bracket-bent-3d.step"]


def sample_files() -> list[str]:
    return SAMPLES


def analyze_sample(pid: str, name: str) -> dict:
    """Reads one of the bundled sample files where it sits, so its bytes never travel."""
    if name not in SAMPLES:
        raise ValueError("not a sample file")
    return analyze(pid, name, base64.b64encode((HERE / "samples" / name).read_bytes()).decode())


def analyze(pid: str, name: str, b64: str, units: str = "auto", pdf_width: float | None = None) -> dict:
    data = base64.b64decode(b64)
    ext = Path(name).suffix.lower()
    try:
        if ext == ".pdf":
            geo = estimator.read_pdf(data, name, width_in=pdf_width or None)
        elif ext in (".step", ".stp"):
            geo = estimator.read_step_file(data, name)
        else:
            geo = estimator.read_dxf(data, name, units_override=units)
    except Exception as err:  # a damaged file must not stop the rest of the job
        geo = estimator.PartGeometry(name=name, units_note="", source=ext.lstrip(".").upper() or "?")
        geo.blocked = f"The file couldn't be read ({err.__class__.__name__}). It may be damaged or not really a {ext.lstrip('.').upper()} file."
    _parts[pid] = geo
    return {
        "id": pid, "name": name, "source": geo.source, "blocked": geo.blocked,
        "units_note": geo.units_note, "width": geo.width, "height": geo.height,
        "cut_length": geo.cut_length, "pierces": geo.pierces, "holes": geo.holes,
        "net_area": geo.net_area, "model_thickness": geo.model_thickness,
        "drawing_width": geo.drawing_width, "warnings": geo.warnings,
        "contours": [{"role": c.role, "closed": c.closed, "points": _thin(c.points)} for c in geo.contours],
        "reference_lines": [_thin(r) for r in geo.reference_lines],
    }


def forget(pid: str) -> bool:
    return _parts.pop(pid, None) is not None


def price(job: dict) -> dict:
    """job: {parts: [{id, material, thickness, qty}], rates: [rows], settings: {...}}"""
    settings = job["settings"]
    rows = job["rates"]
    lines, out = [], []
    for p in job["parts"]:
        geo = _parts.get(p["id"])
        if geo is None or geo.blocked:
            out.append({"id": p["id"], "priced": False})
            continue
        rate = next((r for r in rows if r["material"] == p["material"] and str(r["thickness"]) == str(p["thickness"])), None)
        if rate is None:
            out.append({"id": p["id"], "priced": False, "reason": "No rate for that material and thickness."})
            continue
        q = estimator.estimate(geo, rate, int(p["qty"]), {**settings, "setup_per_job": 0.0, "minimum_charge": 0.0})
        notes = list(q["warnings"])
        t = float(rate["thickness_in"])
        if geo.model_thickness and abs(geo.model_thickness - t) > 0.01 * max(geo.model_thickness, 0.01):
            notes.append(f"The model is {geo.model_thickness:.4f} in thick but {p['thickness']} ({t:.4f} in) is selected.")
        lines.append({**q, "id": p["id"]})
        out.append({"id": p["id"], "priced": True, "notes": notes, **{k: q[k] for k in (
            "run_min_each", "cut_min_each", "pierce_min_each", "machine_each", "material_each", "weight_each",
            "each", "qty", "per_sheet", "sheets", "sheet")}})
    tot = estimator.job_total(lines, settings)
    return {"parts": out, "total": tot}


def quote_pdf(job: dict) -> str:
    """Prices the job and returns the customer quote as base64 PDF."""
    priced = price(job)
    by_id = {p["id"]: p for p in priced["parts"]}
    lines = []
    for p in job["parts"]:
        r, geo = by_id.get(p["id"]), _parts.get(p["id"])
        if not r or not r["priced"]:
            continue
        lines.append({"part": geo.name, "qty": r["qty"], "each": r["each"], "amount": r["each"] * r["qty"],
                      "description": f"{p['material']} {p['thickness']}. {geo.cut_length:.1f} in cut, {geo.pierces} pierces, "
                                     f"{geo.width:.3f} x {geo.height:.3f} in."})
    info = {"shop": job["settings"].get("shop_name", ""), "customer": job.get("customer", ""), "reference": job.get("reference", ""),
            "quote_no": job.get("quote_no") or quote_doc.default_quote_no(), "valid_days": job["settings"].get("valid_days", 30),
            "lead_days": job["settings"].get("lead_days", 5)}
    return base64.b64encode(quote_doc.build_quote_pdf(info, lines, priced["total"])).decode()


def default_quote_no() -> str:
    return quote_doc.default_quote_no()
