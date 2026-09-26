import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from trueforge_sdk import TrueForge
from trueforge_sdk.events import is_event_delta, merge_event_delta

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

AGENT_NAME = "quoteforge"
QUOTE_ID_NOTE = '[Demo run: use quote_id "Q-DEMO-001".]'

def find_call(events: dict, tool_call_id: str) -> tuple[str, str]:
    for e in events.values():
        if e.type == "model.message":
            for tc in e.tool_calls or []:
                if tc.id == tool_call_id:
                    return tc.function.name, tc.function.arguments
    return "?", "?"

def find_breakdown(content) -> dict | None:
    if isinstance(content, dict):
        if "line_items" in json.dumps(content) and "total" in content:
            return content
        return next((b for v in content.values() if (b := find_breakdown(v))), None)
    if isinstance(content, list):
        return next((b for v in content if (b := find_breakdown(v))), None)
    if isinstance(content, str) and "line_items" in content:
        decoder = json.JSONDecoder()
        for i, ch in enumerate(content):
            if ch == "{":
                try:
                    found = find_breakdown(decoder.raw_decode(content, i)[0])
                except ValueError:
                    continue
                if found:
                    return found
    return None

def main():
    enquiry = sys.stdin.read().strip()
    if not enquiry:
        sys.exit(1)

    client = TrueForge(base_url=os.environ.get("TRUEFORGE_BASE_URL") or "http://localhost:8790", timeout=600)
    session_id = client.sessions.create(agent={"name": AGENT_NAME}).data.id
    
    events = {}
    logs = []
    
    def log(msg):
        logs.append(msg)
        
    pending = []
    for event in client.sessions.create_turn_stream(session_id=session_id, input=[{"type": "user.message", "content": f"{enquiry}\n\n{QUOTE_ID_NOTE}"}]):
        if is_event_delta(event):
            base = events.get(event.id)
            if base is not None:
                merge_event_delta(base, event)
            continue
        events[event.id] = event

        if event.type == "tool.response":
            name, args = find_call(events, event.tool_call_id)
            breakdown = find_breakdown(event.content)
            if breakdown:
                log(f"-> {name}(...) - Received costing breakdown")
            else:
                log(f"-> {name}({args})\n<- {str(event.content)[:800]}")
        elif event.type == "tool.approval_required":
            pending.append(event)
        elif event.type == "tool.response_required":
            for ref in event.tool_calls:
                name, args = find_call(events, ref.id)
                log(f"Agent executing tool: {name}")
        elif event.type == "turn.done":
            state = event.state
            if state.status != "done":
                log(f"Turn ended with status {state.status}")

    responses = [e for e in events.values() if e.type == "tool.response"]
    called = [find_call(events, e.tool_call_id)[0] for e in responses]
    breakdowns = [b for e in responses if (b := find_breakdown(e.content))]
    
    status = "SendApproval" if pending else "Done"
    costing = None
    
    if breakdowns:
        rawCosting = breakdowns[-1]
        itemCosting = rawCosting["items"][0]
        
        # Determine stock from check_stock response
        stock_status = "In stock"
        stock_available_kg = 0
        stock_short_kg = 0
        
        for e in responses:
            name, args = find_call(events, e.tool_call_id)
            if name == "check_stock":
                try:
                    res = json.loads(e.content)
                    if isinstance(res, dict):
                        stock_available_kg = res.get("available_kg", 0)
                        stock_short_kg = res.get("short_kg", 0)
                        stock_status = "In stock" if stock_short_kg == 0 else "Low stock"
                except Exception:
                    pass
        
        labour = sum(l["amount"] for l in itemCosting["line_items"] if l["label"] in ['Cutting', 'Bending', 'Welding', 'Drilling'])
        material = next((l["amount"] for l in itemCosting["line_items"] if l["label"] == 'Material'), 0)
        finish = next((l["amount"] for l in itemCosting["line_items"] if l["label"] == 'Finishing'), 0)
        
        achieved_pct = rawCosting["margin_check"]["effective_margin_pct"]
        if achieved_pct is None:
            achieved_pct = rawCosting["margin"]["pct"]
            
        costing = {
            "material": itemCosting["material"],
            "qty": itemCosting["qty"],
            "weight_per_piece_kg": itemCosting["weight_kg_per_piece"],
            "total_weight_kg": itemCosting["weight_kg_total"],
            "nesting": {
                "parts_per_sheet": itemCosting.get("nesting", {}).get("parts_per_sheet", 0),
                "sheets_needed": itemCosting.get("nesting", {}).get("sheets_needed", 0),
                "real_scrap_pct": itemCosting.get("nesting", {}).get("scrap_pct", 0)
            },
            "breakdown": {
                "material_cost": material,
                "labour_cost": labour,
                "finish_cost": finish,
                "overhead": rawCosting["overhead"]["amount"],
                "margin": rawCosting["margin"]["amount"],
                "gst": rawCosting["gst"]["amount"],
                "total": rawCosting["total"]
            },
            "margin_check": {
                "achieved_pct": achieved_pct,
                "floor_pct": rawCosting["margin_check"]["margin_floor_pct"],
                "passed": rawCosting["self_check"]["passed"] and not rawCosting["margin_check"]["below_floor"]
            },
            "stock": {
                "available_kg": stock_available_kg,
                "short_kg": stock_short_kg,
                "status": stock_status
            }
        }
    
    print(json.dumps({
        "status": status,
        "logs": logs,
        "costing": costing
    }))

if __name__ == "__main__":
    main()
