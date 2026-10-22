"""Laser cut estimator: drop in flat-pattern DXFs, pick material and quantity, get cut
time and a price from the shop's own rates.

Run on a shop PC:  streamlit run streamlit_app.py
"""
import io
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

import estimator

HERE = Path(__file__).parent
st.set_page_config(page_title="Laser cut estimator", page_icon="✂", layout="wide")

st.markdown("""
<style>
  .block-container { padding-top: 2.2rem; max-width: 1500px; }
  h1 { font-size: 1.9rem !important; letter-spacing: -0.01em; }
  [data-testid="stMetricValue"] { font-size: 1.35rem; font-variant-numeric: tabular-nums; }
  [data-testid="stMetricLabel"] p { font-size: .82rem; color: #4b5563; }
  .part-head { display:flex; justify-content:space-between; align-items:baseline; gap:1rem; border-bottom:1px solid #d6d9dc; padding-bottom:.35rem; margin-bottom:.6rem; }
  .part-head b { font-size:1.05rem; }
  .part-head span { color:#4b5563; font-size:.85rem; }
  .preview { background:#f7f7f5; border:1px solid #e1e3e5; border-radius:4px; padding:10px; }
  .total-box { border:2px solid #1d2327; border-radius:4px; padding:14px 18px; }
  .total-box .big { font-size:2rem; font-weight:700; font-variant-numeric: tabular-nums; }
  .legend { font-size:.8rem; color:#4b5563; }
  .legend i { display:inline-block; width:9px; height:9px; border-radius:50%; background:#e8590c; margin:0 4px 0 0; vertical-align:middle; }
  @media (max-width: 640px) {
    /* only the fields and figures inside a part pair up; the drawing and the main blocks stay full width */
    [data-testid="stColumn"] [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; gap: .75rem 1rem !important; }
    [data-testid="stColumn"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { flex: 1 1 calc(50% - 1rem) !important; min-width: calc(50% - 1rem) !important; width: auto !important; }
    [data-testid="stMetricValue"] { font-size: 1.15rem; }
  }
</style>
""", unsafe_allow_html=True)

DEFAULT_SETTINGS = {
    "machine_rate_hr": 150.0, "motion_allowance_pct": 15.0, "setup_per_job": 25.0,
    "handling_per_part": 0.50, "material_markup_pct": 20.0, "part_spacing_in": 0.25,
    "minimum_charge": 50.0, "material_basis": "Rectangle the part needs",
}
if "rates" not in st.session_state:
    st.session_state.rates = pd.read_csv(HERE / "rates.csv")
if "settings" not in st.session_state:
    st.session_state.settings = dict(DEFAULT_SETTINGS)
if "use_samples" not in st.session_state:
    st.session_state.use_samples = False


def mins(m):
    s = round(m * 60)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


st.title("Laser cut estimator")
st.caption("Drop in flat-pattern DXF files. The tool measures the cut path and pierces, then prices each part from your rates.")

tab_quote, tab_rates, tab_how = st.tabs(["Quote", "Rates and charges", "How it works"])

with tab_quote:
    top = st.columns([3, 1.2, 1.2])
    job = top[0].text_input("Customer / job reference", placeholder="e.g. Acme Fab, PO 4471")
    files = top[0].file_uploader("DXF files", type=["dxf"], accept_multiple_files=True, label_visibility="collapsed")
    top[1].write("")
    if top[1].button("Load the 3 sample parts", use_container_width=True):
        st.session_state.use_samples = True
    top[2].write("")
    if st.session_state.use_samples and top[2].button("Clear samples", use_container_width=True):
        st.session_state.use_samples = False
        st.rerun()

    sources = [(f.name, f.getvalue()) for f in files or []]
    if st.session_state.use_samples:
        sources += [(p.name, p.read_bytes()) for p in sorted((HERE / "samples").glob("*.dxf"))]

    rates = st.session_state.rates.dropna(subset=["material", "thickness"])
    settings = st.session_state.settings
    materials = list(dict.fromkeys(rates["material"]))
    lines = []

    if not sources:
        st.info("Upload one or more DXF files above, or load the sample parts to see how it works.")

    for i, (name, data) in enumerate(sources):
        with st.container(border=True):
            left, right = st.columns([1, 1.6], gap="large")
            with right:
                c = st.columns([1.3, 1, 0.7, 1])
                mat = c[0].selectbox("Material", materials, key=f"m{i}{name}")
                thick_opts = list(rates.loc[rates["material"] == mat, "thickness"])
                thk = c[1].selectbox("Thickness", thick_opts, index=min(3, len(thick_opts) - 1), key=f"t{i}{name}")
                qty = c[2].number_input("Qty", min_value=1, value=10, step=1, key=f"q{i}{name}")
                units = c[3].selectbox("Drawing units", ["auto", "inches", "millimetres"], key=f"u{i}{name}")
            try:
                geo = estimator.read_dxf(data, name, units_override=units)
            except Exception as err:  # a corrupt upload shouldn't take the whole quote down
                right.error(f"Couldn't read {name}: {err}")
                continue
            rate = rates[(rates["material"] == mat) & (rates["thickness"] == thk)].iloc[0].to_dict()
            q = estimator.estimate(geo, rate, int(qty), {**settings, "setup_per_job": 0.0, "minimum_charge": 0.0})
            lines.append({**q, "name": name, "material": mat, "thickness": thk, "geo": geo})

            with left:
                st.markdown(f'<div class="part-head"><b>{name}</b><span>{geo.width:.3f} × {geo.height:.3f} in</span></div>', unsafe_allow_html=True)
                st.markdown(f'<div class="preview">{estimator.svg_preview(geo)}</div>', unsafe_allow_html=True)
                st.markdown('<div class="legend"><i></i>where each cut starts (one pierce each) &nbsp;·&nbsp; dashed orange = open contour</div>', unsafe_allow_html=True)
            with right:
                m = st.columns(4)
                m[0].metric("Cut length", f"{geo.cut_length:.2f} in")
                m[1].metric("Pierces", f"{geo.pierces}", help=f"{geo.holes} holes/cutouts, {geo.parts_in_file} outside profile(s)")
                m[2].metric("Run time each", mins(q["run_min_each"]))
                m[3].metric("Weight each", f"{q['weight_each']:.2f} lb")
                m = st.columns(4)
                m[0].metric("Machine each", f"${q['machine_each']:.2f}")
                m[1].metric("Material each", f"${q['material_each']:.2f}")
                m[2].metric("Price each", f"${q['each']:.2f}")
                m[3].metric(f"Line total ({int(qty)})", f"${q['each'] * qty:,.2f}")
                st.caption(f"Units: {geo.units_note}")
                notes = geo.warnings + q["warnings"]
                if notes:
                    st.warning("**Check before quoting**\n" + "\n".join(f"- {w}" for w in notes))

    if lines:
        tot = estimator.job_total(lines, settings)
        st.subheader("Job summary")
        a, b = st.columns([2.2, 1], gap="large")
        table = pd.DataFrame([{
            "Part": l["name"], "Material": f"{l['material']} {l['thickness']}", "Qty": l["qty"],
            "Cut length (in)": round(l["geo"].cut_length, 2), "Pierces": l["geo"].pierces,
            "Run time each": mins(l["run_min_each"]), "Price each ($)": round(l["each"], 2),
            "Line total ($)": round(l["each"] * l["qty"], 2)} for l in lines])
        a.dataframe(table, hide_index=True, use_container_width=True)
        min_note = f"<br>Minimum charge of ${settings['minimum_charge']:,.2f} applied" if tot["minimum_applied"] else ""
        b.markdown(f'<div class="total-box">Parts ${tot["parts"]:,.2f}<br>Setup ${tot["setup"]:,.2f}{min_note}'
                   f'<div class="big">${tot["total"]:,.2f}</div>Total machine time {mins(tot["run_min"])}</div>', unsafe_allow_html=True)
        out = io.StringIO()
        out.write(f"Quote,{job or ''}\nDate,{date.today():%m/%d/%Y}\n\n")
        table.to_csv(out, index=False)
        out.write(f"\nSetup,,,,,,,{tot['setup']:.2f}\nJob total,,,,,,,{tot['total']:.2f}\n")
        b.download_button("Download quote (CSV, opens in Excel)", out.getvalue(), file_name=f"quote-{date.today():%Y%m%d}.csv", use_container_width=True)

with tab_rates:
    st.markdown("These are **example numbers** so the demo works. In your version they come from your own spreadsheet, and you change them here or in Excel without touching the code.")
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
    c = st.columns(2)
    c[0].download_button("Download rates (CSV)", edited.to_csv(index=False), file_name="rates.csv")
    up = c[1].file_uploader("Load your own rates CSV", type=["csv"], label_visibility="collapsed")
    if up is not None:
        st.session_state.rates = pd.read_csv(up)
        st.rerun()

    st.subheader("Shop charges")
    s = st.session_state.settings
    c = st.columns(4)
    s["machine_rate_hr"] = c[0].number_input("Machine rate ($/hr)", value=s["machine_rate_hr"], step=5.0)
    s["motion_allowance_pct"] = c[1].number_input("Head travel allowance (%)", value=s["motion_allowance_pct"], step=1.0, help="Added to cut + pierce time for rapid moves between features.")
    s["setup_per_job"] = c[2].number_input("Setup per job ($)", value=s["setup_per_job"], step=5.0)
    s["minimum_charge"] = c[3].number_input("Minimum job charge ($)", value=s["minimum_charge"], step=5.0)
    c = st.columns(4)
    s["handling_per_part"] = c[0].number_input("Handling per part ($)", value=s["handling_per_part"], step=0.25)
    s["material_markup_pct"] = c[1].number_input("Material markup (%)", value=s["material_markup_pct"], step=1.0)
    s["part_spacing_in"] = c[2].number_input("Spacing between parts (in)", value=s["part_spacing_in"], step=0.05)
    s["material_basis"] = c[3].selectbox("Charge material by", ["Rectangle the part needs", "Net part area"],
                                         index=0 if s["material_basis"] == "Rectangle the part needs" else 1)

with tab_how:
    st.markdown((HERE / "how_it_works.md").read_text(encoding="utf-8"))
