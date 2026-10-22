"""The customer quote as a one-page PDF, laid out like a normal shop quotation: shop name
and quote details at the top, the customer, a line per part, totals and terms."""
from datetime import date, timedelta

from fpdf import FPDF

INK = (29, 35, 39)
GREY = (95, 100, 106)
RULE = (205, 208, 211)


def _money(v):
    return f"${v:,.2f}"


def build_quote_pdf(info: dict, lines: list[dict], totals: dict) -> bytes:
    """info: shop, customer, reference, quote_no, valid_days, lead_days.
    lines: part, description, qty, each, amount.  totals: from estimator.job_total."""
    pdf = FPDF(format="letter", unit="pt")
    pdf.set_auto_page_break(True, margin=54)
    pdf.set_margins(54, 54, 54)
    pdf.add_page()
    pdf.set_title(f"Quotation {info['quote_no']}")
    pdf.set_author(info["shop"] or "Laser cut estimator")
    pdf.set_creator("Laser cut estimator")
    w = pdf.w - 108
    today = date.today()

    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 17)
    pdf.cell(w * 0.6, 22, info["shop"] or "Your shop name")
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(w * 0.4, 22, "QUOTATION", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*INK)
    pdf.set_line_width(1.2)
    pdf.line(54, pdf.get_y() + 6, 54 + w, pdf.get_y() + 6)
    pdf.ln(18)

    def pair(label, value, x, y):
        pdf.set_xy(x, y)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*GREY)
        pdf.cell(80, 13, label.upper())
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*INK)
        pdf.cell(160, 13, value)

    y0 = pdf.get_y()
    pair("Customer", info["customer"] or "-", 54, y0)
    pair("Reference", info["reference"] or "-", 54, y0 + 16)
    pair("Quote no.", info["quote_no"], 54 + w * 0.58, y0)
    pair("Date", f"{today:%m/%d/%Y}", 54 + w * 0.58, y0 + 16)
    pair("Valid until", f"{today + timedelta(days=int(info['valid_days'])):%m/%d/%Y}", 54 + w * 0.58, y0 + 32)
    pdf.set_y(y0 + 62)

    cols = [("Item", 0.06, "L"), ("Part / description", 0.52, "L"), ("Qty", 0.1, "R"), ("Unit price", 0.16, "R"), ("Amount", 0.16, "R")]
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*GREY)
    for name, frac, al in cols:
        pdf.cell(w * frac, 16, name.upper(), align=al)
    pdf.ln(16)
    pdf.set_draw_color(*RULE)
    pdf.set_line_width(0.6)
    for i, ln in enumerate(lines, 1):
        y = pdf.get_y()
        pdf.line(54, y, 54 + w, y)
        pdf.set_y(y + 5)
        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(w * 0.06, 14, str(i))
        pdf.cell(w * 0.52, 14, ln["part"])
        pdf.cell(w * 0.1, 14, f"{ln['qty']:,}", align="R")
        pdf.cell(w * 0.16, 14, _money(ln["each"]), align="R")
        pdf.cell(w * 0.16, 14, _money(ln["amount"]), align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(54 + w * 0.06)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*GREY)
        pdf.multi_cell(w * 0.52, 11, ln["description"], new_x="LMARGIN", new_y="NEXT")
        pdf.ln(5)
    pdf.line(54, pdf.get_y(), 54 + w, pdf.get_y())
    pdf.ln(8)

    def total_row(label, value, bold=False):
        pdf.set_x(54 + w * 0.58)
        pdf.set_font("Helvetica", "B" if bold else "", 11 if bold else 10)
        pdf.set_text_color(*INK)
        pdf.cell(w * 0.26, 16, label, align="R")
        pdf.cell(w * 0.16, 16, value, align="R", new_x="LMARGIN", new_y="NEXT")

    total_row("Parts", _money(totals["parts"]))
    total_row("Setup", _money(totals["setup"]))
    if totals["minimum_applied"]:
        total_row("Minimum charge adjustment", _money(totals["total"] - totals["subtotal"]))
    y = pdf.get_y() + 2
    pdf.set_draw_color(*INK)
    pdf.set_line_width(1)
    pdf.line(54 + w * 0.66, y, 54 + w, y)
    pdf.ln(6)
    total_row("Total", _money(totals["total"]), bold=True)

    pdf.ln(22)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*GREY)
    terms = (f"Lead time {int(info['lead_days'])} working days from order and approved files. "
             "Prices exclude sales tax and freight. Parts are cut from the files supplied; please check sizes and quantities before ordering.")
    pdf.multi_cell(w, 13, terms, align="L")
    return bytes(pdf.output())


def default_quote_no():
    return f"Q{date.today():%y%m%d}-01"
