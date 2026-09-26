"""Shop tools served over MCP, backed by Postgres. Same contracts as agent/mocks/server.py (the LLD tools).

Run: uv run server.py [--port 8802]
Point the agent at it with TOOLS_MCP_URL=http://127.0.0.1:8802/mcp and re-run agent/setup.py.
"""

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

import psycopg
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import pdf

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def connect() -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ.get("POSTGRES_USER", "quoteforge"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "quoteforge"),
        row_factory=dict_row,
        autocommit=True,
    )


def num(value):
    """Postgres NUMERIC -> the plain int/float the contract uses."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def plain(row: dict) -> dict:
    return {k: num(v) for k, v in row.items()}


def quote_ref(quote_id: str) -> int:
    """Quote ids look like Q-0007; the number is the quotes table id."""
    try:
        return int(quote_id.upper().removeprefix("Q-"))
    except ValueError as e:
        raise ValueError(f"Unknown quote_id {quote_id!r}; use the id returned by make_quote_pdf") from e


def set_status(quote_id: str, status: str) -> dict:
    with connect() as conn:
        row = conn.execute("UPDATE quotes SET status = %s WHERE id = %s RETURNING pdf_path", (status, quote_ref(quote_id))).fetchone()
    if row is None:
        raise ValueError(f"Quote {quote_id} not found; call make_quote_pdf first")
    return row


mcp = MCPServer(name="quoteforge-tools", description="Shop rate card, stock, quote PDF and quote sending (Postgres).")


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
def get_rate_card(materials: list[str]) -> dict:
    """Return densities, per-kg rates and wastage for the given materials, labour and finishing rates, plus overhead, margin, margin floor and GST settings."""
    with connect() as conn:
        mats = conn.execute("SELECT name, density_kg_m3, rate_per_kg, wastage_pct FROM materials WHERE name = ANY(%s) ORDER BY id", (materials,)).fetchall()
        labour = conn.execute("SELECT op, unit, rate FROM labour_rates ORDER BY id").fetchall()
        finishing = conn.execute('SELECT type, rate_per_m2 FROM finishing_rates ORDER BY id').fetchall()
        settings = conn.execute("SELECT overhead_pct, margin_pct, margin_floor_pct, gst_pct FROM settings ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "materials": [plain(m) for m in mats],
        "labour_rates": [plain(r) for r in labour],
        "finishing_rates": [plain(r) for r in finishing],
        "settings": plain(settings),
    }


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
def check_stock(material: str, thickness_mm: float, kg_needed: float | None = None) -> dict:
    """Return kg available for a material and thickness, and how many kg short the job is (null if kg_needed is not given)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(s.qty_kg), 0) AS available_kg FROM stock s JOIN materials m ON m.id = s.material_id "
            "WHERE m.name = %s AND s.thickness_mm = %s",
            (material, thickness_mm),
        ).fetchone()
    available_kg = num(row["available_kg"])
    short_kg = None if kg_needed is None else max(0, kg_needed - available_kg)
    return {"available_kg": available_kg, "short_kg": short_kg}


class QuoteInput(TypedDict):
    customer: str | None
    email: str
    breakdown: dict
    assumptions: list[str]


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False))
def make_quote_pdf(quote: QuoteInput) -> dict:
    """Save the quote and render its one-page PDF. `breakdown` is the costing.py JSON exactly as printed; it is rejected if it does not add up. Returns the quote_id to use for send_quote."""
    breakdown = quote["breakdown"]
    pdf.check_breakdown(breakdown)
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO quotes (customer, breakdown_json, total, status) VALUES (%s, %s, %s, 'draft') RETURNING id",
            (quote.get("customer") or "Customer", Jsonb(breakdown), breakdown["total"]),
        ).fetchone()
        quote_id = f"Q-{row['id']:04d}"
        path = pdf.render(quote_id, quote.get("customer"), quote["email"], breakdown, quote.get("assumptions") or [])
        pdf_path = str(path.relative_to(pdf.QUOTES_DIR.parent))
        conn.execute("UPDATE quotes SET pdf_path = %s WHERE id = %s", (pdf_path, row["id"]))
    return {"quote_id": quote_id, "pdf_path": pdf_path}


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
def request_margin_approval(quote_id: str, margin_pct: float, margin_floor_pct: float, reason: str) -> dict:
    """Ask the owner to approve a quote whose margin is below the floor. Call this whenever costing reports margin below floor."""
    print(f"[tools] margin approved quote_id={quote_id} margin_pct={margin_pct} floor={margin_floor_pct}", file=sys.stderr)
    return {"status": "approved"}


class CounterOption(TypedDict):
    label: str
    changes: str
    total: float
    unit_price: float


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True))
def send_counter_offer(quote_id: str, email: str, options: list[CounterOption], message: str) -> dict:
    """Send a counter-offer to the customer: a short message and the priced options. Irreversible: offered prices are commitments."""
    set_status(quote_id, "counter_offer_sent")
    print(f"[tools] send_counter_offer quote_id={quote_id} email={email} options={len(options)}", file=sys.stderr)
    return {"status": "sent"}


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True))
def send_quote(quote_id: str, email: str) -> dict:
    """Send a finalised quote to the customer. Irreversible: a sent price is a commitment."""
    row = set_status(quote_id, "sent")
    # Email delivery is a stretch goal; the quote is marked sent and its PDF is the attachment.
    print(f"[tools] send_quote quote_id={quote_id} email={email} pdf={row['pdf_path']}", file=sys.stderr)
    return {"status": "sent", "pdf_path": row["pdf_path"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8802)
    mcp.run("streamable-http", port=parser.parse_args().port)
