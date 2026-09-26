"""Mock shop tools served over MCP, matching the LLD tool contracts.

Swapped for the real tools server by changing TOOLS_MCP_URL.
Run: uv run mocks/server.py [--port 8801]
"""

import argparse
import itertools
import sys
from pathlib import Path
from typing import TypedDict

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

# The real PDF renderer, so the mock produces the same document.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import pdf  # noqa: E402

# Seed values from DESIGN.md (dummy data).
MATERIALS = {
    "MS": {"name": "MS", "density_kg_m3": 7850, "rate_per_kg": 62, "wastage_pct": 5},
    "SS304": {"name": "SS304", "density_kg_m3": 8000, "rate_per_kg": 230, "wastage_pct": 5},
    "AL": {"name": "AL", "density_kg_m3": 2700, "rate_per_kg": 280, "wastage_pct": 5},
}
LABOUR_RATES = [
    {"op": "cutting", "unit": "per_piece", "rate": 15},
    {"op": "bending", "unit": "per_bend", "rate": 10},
    {"op": "welding", "unit": "per_m", "rate": 120},
    {"op": "drilling", "unit": "per_hole", "rate": 5},
]
FINISHING_RATES = [
    {"type": "powder_coat", "rate_per_m2": 180},
    {"type": "paint", "rate_per_m2": 90},
    {"type": "galvanise", "rate_per_m2": 250},
]
SETTINGS = {"overhead_pct": 10, "margin_pct": 20, "margin_floor_pct": 12, "gst_pct": 18}

# (material, thickness_mm) -> kg on hand. SS304 is deliberately low for demo scenario 4.
STOCK_KG = {("MS", 8): 500, ("SS304", 3): 20}

mcp = MCPServer(name="quoteforge-tools", description="Shop rate card, stock and quote sending (mock).")


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
def get_rate_card(materials: list[str]) -> dict:
    """Return densities, per-kg rates and wastage for the given materials, labour and finishing rates, plus overhead, margin, margin floor and GST settings."""
    return {
        "materials": [MATERIALS[m] for m in materials if m in MATERIALS],
        "labour_rates": LABOUR_RATES,
        "finishing_rates": FINISHING_RATES,
        "settings": SETTINGS,
    }


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
def check_stock(material: str, thickness_mm: float, kg_needed: float | None = None) -> dict:
    """Return kg available for a material and thickness, and how many kg short the job is (null if kg_needed is not given)."""
    available_kg = STOCK_KG.get((material, thickness_mm), 0)
    short_kg = None if kg_needed is None else max(0, kg_needed - available_kg)
    return {"available_kg": available_kg, "short_kg": short_kg}


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
def request_margin_approval(quote_id: str, margin_pct: float, margin_floor_pct: float, reason: str) -> dict:
    """Ask the owner to approve a quote whose margin is below the floor. Call this whenever costing reports margin below floor."""
    print(f"[mock] request_margin_approval quote_id={quote_id} margin_pct={margin_pct} floor={margin_floor_pct}", file=sys.stderr)
    return {"status": "approved"}


class CounterOption(TypedDict):
    label: str
    changes: str
    total: float
    unit_price: float


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True))
def send_counter_offer(quote_id: str, email: str, options: list[CounterOption], message: str) -> dict:
    """Send a counter-offer to the customer: a short message and the priced options. Irreversible: offered prices are commitments."""
    print(f"[mock] send_counter_offer quote_id={quote_id} email={email} options={len(options)}", file=sys.stderr)
    return {"status": "sent"}


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True))
def send_quote(quote_id: str, email: str) -> dict:
    """Send a finalised quote to the customer. Irreversible: a sent price is a commitment."""
    print(f"[mock] send_quote quote_id={quote_id} email={email}", file=sys.stderr)
    return {"status": "sent", "pdf_path": PDF_PATHS.get(quote_id)}


class QuoteInput(TypedDict):
    customer: str | None
    email: str
    spec: dict
    breakdown: dict
    assumptions: list[str]


PDF_PATHS: dict[str, str] = {}
_quote_numbers = itertools.count(9001)  # mock ids stay clear of real quote ids


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False))
def make_quote_pdf(quote: QuoteInput) -> dict:
    """Save the quote and render its one-page PDF. `spec` is the validated spec; `breakdown` is the costing.py JSON exactly as printed. Rejected if it does not add up or does not match the shop rate card. Returns the quote_id to use for send_quote."""
    pdf.check_breakdown(quote["breakdown"])
    pdf.verify_against_rate_card(quote["spec"], quote["breakdown"], get_rate_card(sorted({i["material"] for i in quote["spec"]["items"]})))
    quote_id = f"Q-{next(_quote_numbers):04d}"
    path = pdf.render(quote_id, quote.get("customer"), quote["email"], quote["breakdown"], quote.get("assumptions") or [])
    PDF_PATHS[quote_id] = str(path.relative_to(pdf.QUOTES_DIR.parent))
    return {"quote_id": quote_id, "pdf_path": PDF_PATHS[quote_id]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8801)
    mcp.run("streamable-http", port=parser.parse_args().port)
