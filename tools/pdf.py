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


def verify_against_rate_card(spec: dict, breakdown: dict, rate_card: dict) -> None:
    """Re-run costing.py on the spec with the shop's own rate card; the quote must match it exactly.

    Catches a breakdown built from rates the model wrote itself instead of calling get_rate_card.
    """
    spec = {**spec, "target_price": breakdown["margin_check"].get("target_price")}
    expected = json.loads(costing.to_json(costing.compute(
        spec, rate_card, breakdown.get("wastage_mode", "flat"),
        price_at_target=bool(breakdown.get("priced_at_target")), at_floor=bool(breakdown.get("priced_at_floor")))))
    if d(expected["total"]) != d(breakdown["total"]):
        raise ValueError(f"Breakdown does not match the shop rate card (expected total {expected['total']}, got {breakdown['total']}). "
                         "Call get_rate_card, put its output into the costing input unchanged, and re-run costing.py.")


def rs(x) -> str:
    return f"Rs {d(x):,.2f}"


def latin1(text: str) -> str:
    # Core PDF fonts are Latin-1 only.
    for a, b in {"₹": "Rs ", "×": "x", "–": "-", "—": "-", "’": "'", "‘": "'", "“": '"', "”": '"', "…": "..."}.items():
        text = text.replace(a, b)
    return text.encode("latin-1", "replace").decode("latin-1")


def render(quote_id: str, customer: str | None, email: str, breakdown: dict, assumptions: list[str], today: date | None = None) -> Path:
    today = today or date.today()
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_margins(15, 15, 15)

    def text(s: str, size: int = 10, style: str = "", h: float = 6, align: str = "L", w: float = 0, ln: bool = True):
        pdf.set_font("Helvetica", style, size)
        pdf.cell(w, h, latin1(s), align=align, new_x="LMARGIN" if ln else "RIGHT", new_y="NEXT" if ln else "TOP")

    text(SHOP_NAME, 16, "B", 9)
    text("Fabrication quotation", 11, "", 7)
    pdf.ln(2)
    text(f"Quote {quote_id}    Date {today:%d %b %Y}    Valid until {today + timedelta(days=VALID_DAYS):%d %b %Y}")
    text(f"To: {customer or 'Customer'} <{email}>")
    pdf.ln(3)

    for item in breakdown["items"]:
        dims = " x ".join(str(x) for x in item["dimensions_mm"])
        text(f"{item['name']} - {item['material']} {dims} mm, qty {item['qty']}", 11, "B", 7)
        for line in item["line_items"]:
            text(f"  {line['label']}: {line['detail']}", w=140, ln=False)
            text(rs(line["amount"]), align="R")
        pdf.ln(1)

    overheads_profit = d(breakdown["overhead"]["amount"]) + d(breakdown["margin"]["amount"])
    rows = [("Overheads and profit", overheads_profit, ""), ("Subtotal (before GST)", breakdown["price_before_gst"], "B"),
            (f"GST {breakdown['gst']['pct']}%", breakdown["gst"]["amount"], ""), ("Total", breakdown["total"], "B")]
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    for label, amount, style in rows:
        text(label, style=style, w=140, ln=False)
        text(rs(amount), style=style, align="R")

    pdf.ln(4)
    text(f"This quotation is valid for {VALID_DAYS} days. Prices include materials, labour and finishing as listed.", 9)
    if assumptions:
        pdf.ln(2)
        text("Assumptions", 10, "B")
        for a in assumptions:
            pdf.set_font("Helvetica", "", 9)
            pdf.multi_cell(0, 5, latin1(f"- {a}"), new_x="LMARGIN", new_y="NEXT")

    QUOTES_DIR.mkdir(exist_ok=True)
    path = QUOTES_DIR / f"{quote_id}.pdf"
    pdf.output(str(path))
    return path
