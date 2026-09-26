"""One-page quote PDF from a costing.py breakdown. Shared by the tools server and the mock."""

import json
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fpdf import FPDF

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
import costing  # noqa: E402  the same tested script the agent runs in the sandbox

SHOP_NAME = "Akshat Engineering & Fabrication Works"
VALID_DAYS = 7
QUOTES_DIR = Path(__file__).resolve().parents[1] / "quotes"


def d(x) -> Decimal:
    return Decimal(str(x))


def check_breakdown(b: dict) -> None:
    """Refuse a breakdown that does not add up: it must be costing.py output passed through unchanged."""
    errors = []
    if not b.get("self_check", {}).get("passed"):
        errors.append("self_check did not pass")
    for item in b["items"]:
        if d(item["item_cost"]) != sum(d(line["amount"]) for line in item["line_items"]):
            errors.append(f"{item['name']}: line items do not sum to item_cost")
    if d(b["cost_subtotal"]) != sum(d(i["item_cost"]) for i in b["items"]):
        errors.append("cost_subtotal is not the sum of item costs")
    if d(b["price_before_gst"]) != d(b["cost_subtotal"]) + d(b["overhead"]["amount"]) + d(b["margin"]["amount"]):
        errors.append("price_before_gst is not cost + overhead + margin")
    if d(b["total"]) != d(b["price_before_gst"]) + d(b["gst"]["amount"]):
        errors.append("total is not price_before_gst + GST")
    if errors:
        raise ValueError("Breakdown rejected; pass the costing.py output unchanged. " + "; ".join(errors))


def verify_against_rate_card(spec: dict, breakdown: dict, rate_card: dict) -> dict:
    """Re-run costing.py on the spec with the shop's own rate card; the quote must match it exactly.

    Catches a breakdown built from rates the model wrote itself instead of calling get_rate_card.
    Returns the verified breakdown (same numbers, plus per-line qty/unit/rate for the quote table).
    """
    spec = {**spec, "target_price": breakdown["margin_check"].get("target_price")}
    expected = json.loads(costing.to_json(costing.compute(
        spec, rate_card, breakdown.get("wastage_mode", "flat"),
        price_at_target=bool(breakdown.get("priced_at_target")), at_floor=bool(breakdown.get("priced_at_floor")))))
    if d(expected["total"]) != d(breakdown["total"]):
        raise ValueError(f"Breakdown does not match the shop rate card (expected total {expected['total']}, got {breakdown['total']}). "
                         "Call get_rate_card, put its output into the costing input unchanged, and re-run costing.py.")
    return expected


# Letterhead details are dummy placeholders for the demo.
SHOP_ADDRESS = "Plot No. 12, Sector 3, Industrial Area, Pithampur, Dhar, Madhya Pradesh 454775"
SHOP_CONTACT = "Phone: +91 98765 43210   Email: quotes@akshatfab.example"
SHOP_GSTIN = "GSTIN: 23AAAAA0000A1Z5 (dummy)   State: 23-Madhya Pradesh"
TERMS = [
    f"This quotation is valid for {VALID_DAYS} days from the date above.",
    "Payment: 50% advance with purchase order, balance before dispatch.",
    "Delivery: 2 to 3 weeks from purchase order confirmation.",
    "Taxes as applicable. GST is included above at the rate shown.",
    "Transportation and installation are not included unless stated.",
    "Work starts only after purchase order confirmation.",
]
LABEL_TEXT = {"Material": "{material} plate", "Cutting": "Cutting", "Bending": "Bending", "Welding": "Welding",
              "Drilling": "Drilling", "Finishing": "{finish} (both faces)"}
FINISH_TEXT = {"powder_coat": "Powder coating", "paint": "Painting", "galvanise": "Galvanising"}

ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
        "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_thousand(n: int) -> str:
    words = []
    if n >= 100:
        words += [ONES[n // 100], "Hundred"]
        n %= 100
    if n >= 20:
        words += [TENS[n // 10]] + ([ONES[n % 10]] if n % 10 else [])
    elif n:
        words.append(ONES[n])
    return " ".join(words)


def _indian_words(n: int) -> str:
    """Integer in words with Indian grouping: crore, lakh, thousand."""
    if n == 0:
        return "Zero"
    parts = []
    for size, name in ((10**7, "Crore"), (10**5, "Lakh"), (10**3, "Thousand")):
        if n >= size:
            parts += [_indian_words(n // size) if size == 10**7 else _below_thousand(n // size), name]
            n %= size
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def amount_in_words(amount) -> str:
    """e.g. 9654.44 -> 'Rupees Nine Thousand Six Hundred Fifty Four and Forty Four Paise Only'."""
    paise_total = int((d(amount) * 100).to_integral_value())
    rupees, paise = divmod(paise_total, 100)
    words = f"Rupees {_indian_words(rupees)}"
    if paise:
        words += f" and {_indian_words(paise)} Paise"
    return words + " Only"


def rs(x) -> str:
    """Indian digit grouping: 123456.5 -> Rs 1,23,456.50."""
    whole, frac = f"{d(x):.2f}".split(".")
    sign, whole = ("-", whole[1:]) if whole.startswith("-") else ("", whole)
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + groups + [tail])
    return f"Rs {sign}{whole}.{frac}"


def qty_text(x) -> str:
    q = d(x).normalize()
    return f"{q:f}" if q == q.to_integral_value() else f"{d(x).quantize(Decimal('0.01')):f}"


def latin1(text: str) -> str:
    # Core PDF fonts are Latin-1 only.
    for a, b in {"₹": "Rs ", "×": "x", "–": "-", "—": "-", "’": "'", "‘": "'", "“": '"', "”": '"', "…": "..."}.items():
        text = text.replace(a, b)
    return text.encode("latin-1", "replace").decode("latin-1")


class QuotePDF(FPDF):
    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(130)
        self.cell(0, 5, "Generated by QuoteForge, approved by owner", align="C")
        self.set_text_color(0)


def render(quote_id: str, customer: str | None, email: str, breakdown: dict, assumptions: list[str], today: date | None = None) -> Path:
    today = today or date.today()
    pdf = QuotePDF(format="A4")
    pdf.set_margins(12, 12, 12)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    W = pdf.epw  # usable width

    def font(size=9, style=""):
        pdf.set_font("Helvetica", style, size)

    def cell(w, h, text, border=0, align="L", style="", size=9, ln=False, fill=False):
        font(size, style)
        pdf.cell(w, h, latin1(str(text)), border=border, align=align, fill=fill,
                 new_x="LMARGIN" if ln else "RIGHT", new_y="NEXT" if ln else "TOP")

    # Title and letterhead
    cell(W, 8, "QUOTATION", align="C", style="B", size=14, ln=True)
    top = pdf.get_y()
    pdf.rect(12, top, W, 30)
    pdf.set_xy(15, top + 2)
    cell(W * 0.62, 7, SHOP_NAME.upper(), style="B", size=13, ln=True)
    for line in (SHOP_ADDRESS, SHOP_CONTACT, SHOP_GSTIN):
        pdf.set_x(15)
        cell(W * 0.62, 5, line, size=8, ln=True)
    # Quote meta, right column of the letterhead box
    meta_x = 12 + W * 0.66
    pdf.line(meta_x, top, meta_x, top + 30)
    for i, (label, value) in enumerate((("Quote No.", quote_id), ("Date", f"{today:%d-%m-%Y}"),
                                        ("Valid until", f"{today + timedelta(days=VALID_DAYS):%d-%m-%Y}"),
                                        ("Place of supply", "23-Madhya Pradesh"))):
        pdf.set_xy(meta_x + 3, top + 2 + i * 6.5)
        cell(24, 6, label, size=8)
        cell(W * 0.34 - 28, 6, value, style="B", size=9)

    # Customer block
    pdf.set_xy(12, top + 30)
    box_top = pdf.get_y()
    pdf.rect(12, box_top, W, 16)
    pdf.set_xy(15, box_top + 1.5)
    cell(W, 5, "Quote To", size=8, ln=True)
    pdf.set_x(15)
    cell(W, 5.5, customer or "Customer", style="B", size=10, ln=True)
    pdf.set_x(15)
    cell(W, 4.5, email, size=8, ln=True)
    pdf.set_y(box_top + 16)

    # Item table
    cols = [("S.No", 11, "C"), ("Description", W - 11 - 20 - 16 - 26 - 30, "L"), ("Qty", 20, "R"),
            ("Unit", 16, "C"), ("Rate", 26, "R"), ("Amount", 30, "R")]
    pdf.set_fill_color(235, 238, 245)
    for name, w, align in cols:
        cell(w, 7, name, border=1, align="C" if name != "Description" else "L", style="B", fill=True)
    pdf.ln(7)

    n = 0
    for item in breakdown["items"]:
        dims = " x ".join(str(x) for x in item["dimensions_mm"])
        cell(W, 6.5, f"{item['name']} - {item['material']} {dims} mm, {item['qty']} Nos", border="LR", style="B", ln=True)
        finish = next((f for f in FINISH_TEXT if f in " ".join(l["detail"] for l in item["line_items"] if l["label"] == "Finishing")), None)
        for line in item["line_items"]:
            n += 1
            desc = LABEL_TEXT.get(line["label"], line["label"]).format(material=item["material"], finish=FINISH_TEXT.get(finish, "Finishing"))
            if line["label"] == "Material":
                desc += " (incl. wastage)" if breakdown.get("wastage_mode") == "flat" else " (full sheets)"
            values = [str(n), desc, qty_text(line["qty"]), line["unit"], rs(line["rate"]).removeprefix("Rs "), rs(line["amount"]).removeprefix("Rs ")]
            for (name, w, align), v in zip(cols, values):
                cell(w, 6.5, v, border="LR", align=align)
            pdf.ln(6.5)
    n += 1
    overheads = d(breakdown["overhead"]["amount"]) + d(breakdown["margin"]["amount"])
    for (name, w, align), v in zip(cols, [str(n), "Overheads and profit", "1", "Lot", rs(overheads).removeprefix("Rs "), rs(overheads).removeprefix("Rs ")]):
        cell(w, 6.5, v, border="LRB", align=align)
    pdf.ln(6.5)

    # Totals (right) and amount in words (left)
    totals_top = pdf.get_y()
    label_w, amount_w = 50, 30
    x_totals = 12 + W - label_w - amount_w
    rows = [("Sub Total (before GST)", breakdown["price_before_gst"], ""),
            (f"GST @ {breakdown['gst']['pct']}%", breakdown["gst"]["amount"], ""),
            ("Grand Total", breakdown["total"], "B")]
    for i, (label, amount, style) in enumerate(rows):
        pdf.set_xy(x_totals, totals_top + i * 7)
        cell(label_w, 7, label, border=1, style=style, size=10 if style else 9)
        cell(amount_w, 7, rs(amount), border=1, align="R", style=style, size=10 if style else 9)
    pdf.set_xy(12, totals_top)
    words_w = x_totals - 12
    pdf.rect(12, totals_top, words_w, 21)
    pdf.set_xy(14, totals_top + 1.5)
    cell(words_w - 4, 5, "Amount in words", size=8, ln=True)
    pdf.set_x(14)
    font(9, "B")
    pdf.multi_cell(words_w - 4, 5, latin1(amount_in_words(breakdown["total"])), new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(totals_top + 21 + 4)

    # Terms and assumptions
    cell(W, 6, "Terms & Conditions", style="B", size=10, ln=True)
    for i, term in enumerate(TERMS, 1):
        font(8.5)
        pdf.multi_cell(W, 4.6, latin1(f"{i}. {term}"), new_x="LMARGIN", new_y="NEXT")
    if assumptions:
        pdf.ln(2)
        cell(W, 6, "Assumptions made for this quote", style="B", size=10, ln=True)
        for a in assumptions:
            font(8.5)
            pdf.multi_cell(W, 4.6, latin1(f"- {a}"), new_x="LMARGIN", new_y="NEXT")

    # Signatory
    pdf.ln(8)
    sign_x = 12 + W - 75
    pdf.set_x(sign_x)
    cell(75, 5, f"For {SHOP_NAME}", align="C", style="B", size=9, ln=True)
    pdf.ln(12)
    pdf.set_x(sign_x)
    pdf.line(sign_x + 10, pdf.get_y(), sign_x + 65, pdf.get_y())
    cell(75, 5, "Authorised Signatory", align="C", size=8, ln=True)

    QUOTES_DIR.mkdir(exist_ok=True)
    path = QUOTES_DIR / f"{quote_id}.pdf"
    pdf.output(str(path))
    return path
