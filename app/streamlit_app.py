"""Laser cut estimator: drop in DXF, PDF or STEP files, pick material and quantity, and get
cut time, a price per part and a customer quote from the shop's own rates.

Run on a shop PC:  streamlit run streamlit_app.py   (or double-click Open estimator.bat)
"""
import io
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

import estimator
import quote_doc

HERE = Path(__file__).parent
st.set_page_config(page_title="Laser cut estimator", page_icon=str(HERE / "icon.png"), layout="wide")

st.markdown("""
<style>
  .block-container { padding-top: 2rem; max-width: 1480px; }
  h1 { font-size: 1.75rem !important; letter-spacing: -0.01em; margin-bottom: 0 !important; }
  h2 { font-size: 1.3rem !important; }
  .part-head { display:flex; justify-content:space-between; align-items:baseline; gap:1rem; border-bottom:1px solid #d6d9dc; padding-bottom:.35rem; margin-bottom:.6rem; }
  .part-head b { font-size:1.02rem; overflow-wrap:anywhere; }
  .part-head span { color:#4b5563; font-size:.85rem; white-space:nowrap; }
  .part-head em { font-style:normal; font-size:.72rem; font-weight:600; letter-spacing:.06em; color:#4b5563; border:1px solid #c9cdd1; border-radius:3px; padding:1px 5px; margin-left:.5rem; vertical-align:2px; }
  .preview { background:#f7f7f5; border:1px solid #e1e3e5; border-radius:4px; padding:12px; }
  table.est { width:100%; border-collapse:collapse; font-size:.9rem; font-variant-numeric: tabular-nums; margin:0 0 .4rem; }
  table.est caption { text-align:left; font-weight:600; font-size:.78rem; letter-spacing:.04em; text-transform:uppercase; color:#4b5563; padding:0 0 .3rem; }
  table.est, table.est tr, table.est td, table.est th { border:0 !important; background:transparent !important; }
  table.est td { padding:.32rem 0; border-bottom:1px solid #e6e8ea !important; vertical-align:baseline; }
  table.est td.v { text-align:right; white-space:nowrap; padding-left:1rem; }
  table.est td small { color:#6b7280; }
  table.est tr.sum td { border-top:1.5px solid #1d2327 !important; border-bottom:0 !important; font-weight:700; }
  .legend { font-size:.8rem; color:#4b5563; margin-top:.35rem; }
  .legend span { display:inline-block; margin-right:.9rem; }
  .legend b { display:inline-block; width:18px; height:0; vertical-align:middle; margin-right:5px; }
  .quote-sheet { border:1px solid #c9cdd1; border-radius:4px; padding:20px 22px; background:#fff; }
  .quote-sheet .top { display:flex; justify-content:space-between; border-bottom:2px solid #1d2327; padding-bottom:.4rem; margin-bottom:.8rem; }
  .quote-sheet .top b { font-size:1.1rem; }
  .quote-sheet .top span { font-weight:700; letter-spacing:.08em; }
  .quote-sheet .meta { display:grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap:.2rem 1.5rem; font-size:.85rem; margin-bottom:.9rem; }
  .quote-sheet .meta i { font-style:normal; color:#6b7280; font-size:.72rem; letter-spacing:.05em; text-transform:uppercase; display:block; }
  .quote-sheet table { width:100%; border-collapse:collapse; font-size:.88rem; font-variant-numeric: tabular-nums; }
  .quote-sheet table, .quote-sheet tr, .quote-sheet td, .quote-sheet th { border:0 !important; background:transparent !important; }
  .quote-sheet th { text-align:left; font-size:.72rem; letter-spacing:.05em; text-transform:uppercase; color:#6b7280; font-weight:600; padding:.2rem .4rem .35rem 0; }
  .quote-sheet td { padding:.45rem .4rem .45rem 0; border-top:1px solid #e1e3e5 !important; vertical-align:top; }
  .quote-sheet td small { color:#6b7280; display:block; margin-top:.1rem; }
  .quote-sheet .r { text-align:right; white-space:nowrap; }
  .quote-sheet .tot td { border-top:0 !important; padding:.15rem .4rem .15rem 0; }
  .quote-sheet .grand td { border-top:1.5px solid #1d2327 !important; font-weight:700; font-size:1.02rem; padding-top:.4rem; }
  .quote-sheet .terms { font-size:.8rem; color:#4b5563; margin-top:.9rem; }
  @media (max-width: 640px) {
    /* only the fields and figures inside a part pair up; the drawing and the main blocks stay full width */
    [data-testid="stColumn"] [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; gap: .75rem 1rem !important; }
    [data-testid="stColumn"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { flex: 1 1 calc(50% - 1rem) !important; min-width: calc(50% - 1rem) !important; width: auto !important; }
    .quote-sheet { padding:14px; }
  }
</style>
""", unsafe_allow_html=True)

DEFAULT_SETTINGS = {
    "machine_rate_hr": 150.0, "motion_allowance_pct": 15.0, "setup_per_job": 25.0, "handling_per_part": 0.50,
    "material_markup_pct": 20.0, "part_spacing_in": 0.25, "minimum_charge": 50.0, "material_basis": "Rectangle the part needs",
    "sheet_w_in": 48.0, "sheet_h_in": 96.0, "sheet_margin_in": 0.5, "valid_days": 30.0, "lead_days": 5.0, "shop_name": "",
}
SAMPLE_SET = ["l-bracket-flat.dxf", "flange-print.pdf", "mounting-plate-3d.step", "l-bracket-bent-3d.step"]

if "rates" not in st.session_state:
    rows, charges = estimator.load_rates_workbook((HERE / "rates.xlsx").read_bytes())
    st.session_state.rates = pd.DataFrame(rows)
    st.session_state.settings = {**DEFAULT_SETTINGS, **charges}
st.session_state.setdefault("use_samples", False)
st.session_state.setdefault("quote_no", quote_doc.default_quote_no())


def mins(m):
    s = round(m * 60)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def money(v):
    return f"${v:,.2f}"


def read_part(name, data, units, pdf_width):
    ext = Path(name).suffix.lower()
    if ext == ".pdf":
        return estimator.read_pdf(data, name, width_in=pdf_width)
    if ext in (".step", ".stp"):
        return estimator.read_step_file(data, name)
    return estimator.read_dxf(data, name, units_override=units)


st.title("Laser cut estimator")
st.caption("Drop in DXF, PDF or STEP files. Each part's cut path and pierces are measured and priced from your own rates, and the quote is ready to send.")

tab_quote, tab_rates, tab_how = st.tabs(["Quote", "Rates and charges", "How it works"])
settings = st.session_state.settings

with tab_quote:
    c = st.columns([1.3, 1.3, 1])
    customer = c[0].text_input("Customer", placeholder="Company name")
    reference = c[1].text_input("Their reference / PO", placeholder="e.g. PO 4471")
    st.session_state.quote_no = c[2].text_input("Quote no.", value=st.session_state.quote_no)

    up, side = st.columns([3, 1.1], vertical_alignment="center")
    files = up.file_uploader("Drawings (DXF, PDF or STEP)", type=["dxf", "pdf", "step", "stp"], accept_multiple_files=True)
    with side:
        if not st.session_state.use_samples:
            if st.button("Try the sample files", use_container_width=True):
                st.session_state.use_samples = True
                st.rerun()
            st.caption("A messy DXF, a customer print (PDF), a STEP plate and a bent bracket.")
        elif st.button("Remove the sample files", use_container_width=True):
            st.session_state.use_samples = False
            st.rerun()

    sources = [(f.name, f.getvalue()) for f in files or []]
    if st.session_state.use_samples:
        sources += [(n, (HERE / "samples" / n).read_bytes()) for n in SAMPLE_SET]

    rates = st.session_state.rates.dropna(subset=["material", "thickness"])
    materials = list(dict.fromkeys(rates["material"]))
    lines = []

    if not sources:
        st.info("Add drawings above, or try the sample files to see how it works.")

    for i, (name, data) in enumerate(sources):
        key = f"{i}-{name}"
        ext = Path(name).suffix.lower().lstrip(".")
        with st.container(border=True):
            left, right = st.columns([1, 1.6], gap="large")
            with right:
                c = st.columns([1.3, 1, 0.7, 1])
                mat = c[0].selectbox("Material", materials, key=f"m{key}")
                opts = rates[rates["material"] == mat]
                thick_opts = list(opts["thickness"])
                units, pdf_width = "auto", None
                if ext == "dxf":
                    units = c[3].selectbox("Drawing units", ["auto", "inches", "millimetres"], key=f"u{key}")
                elif ext == "pdf":
                    pdf_width = c[3].number_input("Real width (in)", min_value=0.0, value=0.0, step=0.125, format="%.3f", key=f"w{key}",
                                                  help="Leave at 0 for a 1:1 print. For a scaled print, enter the part's real overall width.") or None
            try:
                geo = read_part(name, data, units, pdf_width)
            except Exception as err:  # a damaged upload shouldn't take the whole quote down
                right.error(f"Couldn't read {name}: {err}")
                continue
            default_t = min(3, len(thick_opts) - 1)
            if geo.model_thickness:
                default_t = min(range(len(thick_opts)), key=lambda j: abs(float(opts.iloc[j]["thickness_in"]) - geo.model_thickness))
            thk = c[1].selectbox("Thickness", thick_opts, index=default_t, key=f"t{key}")
            qty = c[2].number_input("Qty", min_value=1, value=10, step=1, key=f"q{key}")

            with left:
                size = f"{geo.width:.3f} × {geo.height:.3f} in" if geo.contours else ""
                st.markdown(f'<div class="part-head"><b>{name}<em>{geo.source}</em></b><span>{size}</span></div>', unsafe_allow_html=True)
                if geo.blocked:
                    st.markdown('<div class="preview" style="color:#4b5563;font-size:.88rem;padding:28px 16px;text-align:center">No flat pattern to show yet.</div>', unsafe_allow_html=True)
                if geo.contours:
                    st.markdown(f'<div class="preview">{estimator.svg_preview(geo)}</div>', unsafe_allow_html=True)
                    legend = ['<span><b style="border-top:8px solid #e4e5e4;outline:1px solid #3b4045"></b>part</span>']
                    if geo.reference_lines:
                        legend.append('<span><b style="border-top:2px dashed #0e9aa7"></b>bend / etch line, not cut</span>')
                    if any(cn.role == "open" for cn in geo.contours):
                        legend.append('<span><b style="border-top:2px solid #c81e1e"></b>open outline</span>')
                    st.markdown(f'<div class="legend">{"".join(legend)}</div>', unsafe_allow_html=True)

            with right:
                if geo.blocked:
                    st.warning("**Can't price this file as it is**\n\n" + geo.blocked)
                    continue
                rate = opts[opts["thickness"] == thk].iloc[0].to_dict()
                q = estimator.estimate(geo, rate, int(qty), {**settings, "setup_per_job": 0.0, "minimum_charge": 0.0})
                lines.append({**q, "name": name, "material": mat, "thickness": thk, "geo": geo})
                t = st.columns(2, gap="medium")
                holes = "1 hole or cutout" if geo.holes == 1 else f"{geo.holes} holes or cutouts"
                sheet = (f"{q['per_sheet']} per {q['sheet'][0]:g} × {q['sheet'][1]:g} sheet" if q["per_sheet"] else "doesn't fit a sheet")
                t[0].markdown(f"""<table class="est"><caption>Cut</caption>
<tr><td>Cut length</td><td class="v">{geo.cut_length:.2f} in</td></tr>
<tr><td>Pierces <small>{holes}</small></td><td class="v">{geo.pierces}</td></tr>
<tr><td>Weight each</td><td class="v">{q['weight_each']:.2f} lb</td></tr>
<tr><td>Sheet use <small>{sheet}</small></td><td class="v">{q['sheets']} {'sheet' if q['sheets'] == 1 else 'sheets'}</td></tr>
<tr class="sum"><td>Run time each</td><td class="v">{mins(q['run_min_each'])}</td></tr></table>""", unsafe_allow_html=True)
                t[1].markdown(f"""<table class="est"><caption>Price each</caption>
<tr><td>Machine <small>at ${settings['machine_rate_hr']:.0f}/hr</small></td><td class="v">{money(q['machine_each'])}</td></tr>
<tr><td>Material <small>+{settings['material_markup_pct']:.0f}%</small></td><td class="v">{money(q['material_each'])}</td></tr>
<tr><td>Handling</td><td class="v">{money(settings['handling_per_part'])}</td></tr>
<tr><td>Each</td><td class="v">{money(q['each'])}</td></tr>
<tr class="sum"><td>× {int(qty)}</td><td class="v">{money(q['each'] * qty)}</td></tr></table>""", unsafe_allow_html=True)
                st.caption(f"Units: {geo.units_note}")
                notes = geo.warnings + q["warnings"]
                if geo.model_thickness and abs(geo.model_thickness - float(rate["thickness_in"])) > 0.01 * max(geo.model_thickness, 0.01):
                    notes.append(f"The model is {geo.model_thickness:.4f} in thick but {thk} ({float(rate['thickness_in']):.4f} in) is selected.")
                if notes:
                    st.warning("**Check before quoting**\n" + "\n".join(f"- {w}" for w in notes))

    if lines:
        tot = estimator.job_total(lines, settings)
        st.header("Quote")
        qcol, acol = st.columns([2.3, 1], gap="large")
        rows_html = "".join(
            f"<tr><td>{n}</td><td>{l['name']}<small>{l['material']} {l['thickness']}, {l['geo'].cut_length:.1f} in cut, {l['geo'].pierces} pierces</small></td>"
            f"<td class='r'>{l['qty']}</td><td class='r'>{money(l['each'])}</td><td class='r'>{money(l['each'] * l['qty'])}</td></tr>"
            for n, l in enumerate(lines, 1))
        min_row = (f"<tr class='tot'><td colspan='4' class='r'>Minimum charge adjustment</td><td class='r'>{money(tot['total'] - tot['subtotal'])}</td></tr>"
                   if tot["minimum_applied"] else "")
        qcol.markdown(f"""<div class="quote-sheet">
<div class="top"><b>{settings['shop_name'] or 'Your shop name'}</b><span>QUOTATION</span></div>
<div class="meta"><div><i>Customer</i>{customer or '-'}</div><div><i>Reference</i>{reference or '-'}</div>
<div><i>Quote no.</i>{st.session_state.quote_no}</div><div><i>Valid until</i>{date.today() + timedelta(days=int(settings['valid_days'])):%m/%d/%Y}</div></div>
<table><tr><th>Item</th><th>Part / description</th><th class="r">Qty</th><th class="r">Unit price</th><th class="r">Amount</th></tr>{rows_html}
<tr class="tot"><td colspan="4" class="r">Parts</td><td class="r">{money(tot['parts'])}</td></tr>
<tr class="tot"><td colspan="4" class="r">Setup</td><td class="r">{money(tot['setup'])}</td></tr>{min_row}
<tr class="grand"><td colspan="4" class="r">Total</td><td class="r">{money(tot['total'])}</td></tr></table>
<div class="terms">Lead time {int(settings['lead_days'])} working days from order and approved files. Prices exclude sales tax and freight.</div></div>""", unsafe_allow_html=True)
        with acol:
            st.metric("Quote total", money(tot["total"]))
            st.caption(f"{len(lines)} {'part' if len(lines) == 1 else 'parts'}, {sum(l['qty'] for l in lines):,} pieces, {mins(tot['run_min'])} of laser time")
            pdf = quote_doc.build_quote_pdf(
                {"shop": settings["shop_name"], "customer": customer, "reference": reference, "quote_no": st.session_state.quote_no,
                 "valid_days": settings["valid_days"], "lead_days": settings["lead_days"]},
                [{"part": l["name"], "description": f"{l['material']} {l['thickness']}. {l['geo'].cut_length:.1f} in cut, {l['geo'].pierces} pierces, {l['geo'].width:.3f} x {l['geo'].height:.3f} in.",
                  "qty": l["qty"], "each": l["each"], "amount": l["each"] * l["qty"]} for l in lines], tot)
            st.download_button("Download quote (PDF)", pdf, file_name=f"{st.session_state.quote_no}.pdf", mime="application/pdf", type="primary", use_container_width=True)
            table = pd.DataFrame([{"Part": l["name"], "Material": f"{l['material']} {l['thickness']}", "Qty": l["qty"],
                                   "Cut length (in)": round(l["geo"].cut_length, 2), "Pierces": l["geo"].pierces,
                                   "Run time each (s)": round(l["run_min_each"] * 60), "Unit price": round(l["each"], 2),
                                   "Amount": round(l["each"] * l["qty"], 2)} for l in lines])
            buf = io.StringIO()
            table.to_csv(buf, index=False)
            st.download_button("Download line items (CSV)", buf.getvalue(), file_name=f"{st.session_state.quote_no}.csv", use_container_width=True)

with tab_rates:
    st.header("Rates")
    st.markdown("One row per material and thickness. The numbers here are **examples** so the demo works; yours come from your own cut charts and spreadsheet. "
                "You can edit them here, or in Excel and load the workbook back in.")
    edited = st.data_editor(
        st.session_state.rates, num_rows="dynamic", use_container_width=True, hide_index=True,
        column_config={
            "material": "Material", "thickness": "Thickness",
            "thickness_in": st.column_config.NumberColumn("Thickness (in)", format="%.4f"),
            "feed_ipm": st.column_config.NumberColumn("Cut speed (in/min)"),
            "pierce_sec": st.column_config.NumberColumn("Pierce time (s)", format="%.1f"),
            "density_lb_in3": st.column_config.NumberColumn("Density (lb/in³)", format="%.4f"),
            "price_per_lb": st.column_config.NumberColumn("Material price ($/lb)", format="%.2f"),
        }, key="rates_editor")
    st.session_state.rates = edited
    c = st.columns([1, 1, 2])
    c[0].download_button("Download the Excel workbook", (HERE / "rates.xlsx").read_bytes(), file_name="rates.xlsx", use_container_width=True)
    up = c[2].file_uploader("Load your edited workbook", type=["xlsx"])
    if up is not None and st.session_state.get("loaded_book") != up.file_id:
        rows, charges = estimator.load_rates_workbook(up.getvalue())
        st.session_state.rates = pd.DataFrame(rows)
        st.session_state.settings.update(charges)
        st.session_state.loaded_book = up.file_id
        st.rerun()

    st.header("Shop charges")
    s = st.session_state.settings
    s["shop_name"] = st.text_input("Shop name on the quote", value=s["shop_name"], placeholder="Your shop name")
    c = st.columns(4)
    s["machine_rate_hr"] = c[0].number_input("Machine rate ($/hr)", value=float(s["machine_rate_hr"]), step=5.0)
    s["motion_allowance_pct"] = c[1].number_input("Head travel allowance (%)", value=float(s["motion_allowance_pct"]), step=1.0, help="Added to cut + pierce time for moves between features.")
    s["setup_per_job"] = c[2].number_input("Setup per job ($)", value=float(s["setup_per_job"]), step=5.0)
    s["minimum_charge"] = c[3].number_input("Minimum job charge ($)", value=float(s["minimum_charge"]), step=5.0)
    c = st.columns(4)
    s["handling_per_part"] = c[0].number_input("Handling per part ($)", value=float(s["handling_per_part"]), step=0.25)
    s["material_markup_pct"] = c[1].number_input("Material markup (%)", value=float(s["material_markup_pct"]), step=1.0)
    s["part_spacing_in"] = c[2].number_input("Spacing between parts (in)", value=float(s["part_spacing_in"]), step=0.05)
    bases = ["Rectangle the part needs", "Full sheets used", "Net part area"]
    s["material_basis"] = c[3].selectbox("Charge material by", bases, index=bases.index(s["material_basis"]))
    c = st.columns(4)
    s["sheet_w_in"] = c[0].number_input("Sheet width (in)", value=float(s["sheet_w_in"]), step=12.0)
    s["sheet_h_in"] = c[1].number_input("Sheet length (in)", value=float(s["sheet_h_in"]), step=12.0)
    s["valid_days"] = c[2].number_input("Quote valid for (days)", value=float(s["valid_days"]), step=5.0)
    s["lead_days"] = c[3].number_input("Lead time (working days)", value=float(s["lead_days"]), step=1.0)

with tab_how:
    st.markdown((HERE / "how_it_works.md").read_text(encoding="utf-8"))
