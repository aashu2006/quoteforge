"""The Postgres tools server must be a drop-in for the mock (agent/mocks/server.py).

Needs both servers running: mock on :8801, real on :8802 (with Postgres seeded from db/).
"""

import json
import socket

import anyio
import pytest
from mcp.client.client import Client

MOCK, REAL = "http://127.0.0.1:8801/mcp", "http://127.0.0.1:8802/mcp"


def up(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


pytestmark = pytest.mark.skipif(not (up(8801) and up(8802)), reason="mock (:8801) and real (:8802) tools servers must be running")


async def tools(url: str) -> dict:
    async with Client(url) as c:
        return {t.name: t for t in (await c.list_tools()).tools}


async def call(url: str, name: str, args: dict) -> dict:
    async with Client(url) as c:
        return json.loads((await c.call_tool(name, args)).content[0].text)


def test_same_tools_args_and_annotations():
    mock, real = anyio.run(tools, MOCK), anyio.run(tools, REAL)
    assert set(mock) <= set(real)
    for name, t in mock.items():
        assert real[name].input_schema["properties"].keys() == t.input_schema["properties"].keys(), name
        assert real[name].input_schema.get("required") == t.input_schema.get("required"), name
        for hint in ("read_only_hint", "destructive_hint"):
            assert getattr(real[name].annotations, hint) == getattr(t.annotations, hint), (name, hint)


def test_rate_card_matches_seed():
    args = {"materials": ["MS", "SS304", "AL"]}
    assert anyio.run(call, REAL, "get_rate_card", args) == anyio.run(call, MOCK, "get_rate_card", args)


def scenario1_breakdown() -> dict:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sandbox"))
    import costing
    data = json.loads((Path(__file__).resolve().parents[2] / "sandbox" / "examples" / "scenario1.json").read_text())
    return json.loads(costing.to_json(costing.compute(data["spec"], data["rate_card"])))


@pytest.mark.parametrize("url", [MOCK, REAL])
def test_make_quote_pdf_then_send(url):
    quote = {"customer": "Sharma Industries", "email": "purchase@sharma-industries.example",
             "breakdown": scenario1_breakdown(), "assumptions": ["test"]}
    made = anyio.run(call, url, "make_quote_pdf", {"quote": quote})
    assert made.keys() == {"quote_id", "pdf_path"} and made["pdf_path"].endswith(".pdf")
    sent = anyio.run(call, url, "send_quote", {"quote_id": made["quote_id"], "email": quote["email"]})
    assert sent == {"status": "sent", "pdf_path": made["pdf_path"]}


def test_make_quote_pdf_rejects_tampered_breakdown():
    breakdown = scenario1_breakdown()
    breakdown["total"] = 1.0
    quote = {"customer": None, "email": "x@example.com", "breakdown": breakdown, "assumptions": []}

    async def attempt():
        async with Client(REAL) as c:
            return await c.call_tool("make_quote_pdf", {"quote": quote})
    assert anyio.run(attempt).is_error


@pytest.mark.parametrize("args", [
    {"material": "MS", "thickness_mm": 8, "kg_needed": 65.94},
    {"material": "MS", "thickness_mm": 8, "kg_needed": None},
    {"material": "MS", "thickness_mm": 6, "kg_needed": 10},
])
def test_check_stock_shape(args):
    real = anyio.run(call, REAL, "check_stock", args)
    assert real.keys() == {"available_kg", "short_kg"}
    if args == {"material": "MS", "thickness_mm": 8, "kg_needed": 65.94}:
        assert real == {"available_kg": 500, "short_kg": 0}
