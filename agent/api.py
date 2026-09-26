"""JSON bridge for the UI backend: start a quote from an enquiry, or decide a pending approval gate.

Run: echo '{"enquiry": "..."}' | uv run api.py start
     echo '{"session_id": "...", "decision": "allow"}' | uv run api.py decide
Prints one JSON object on stdout (contract in docs/DESIGN.md, "UI API"). Diagnostics go to stderr.
The pending gate is always read back from TrueForge, never taken from the caller.
"""

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from trueforge_sdk import TrueForge

import pipeline

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BASE_URL = os.environ.get("TRUEFORGE_BASE_URL") or "http://localhost:8790"
GATES = {"request_margin_approval": "margin", "send_quote": "send", "send_counter_offer": "counter_offer"}
SHOP_TOOLS = {"get_rate_card", "check_stock", "make_quote_pdf", "request_margin_approval", "send_quote", "send_counter_offer"}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def run_turn(client: TrueForge, session_id: str, turn_input: list) -> None:
    def consume() -> None:
        for event in client.sessions.create_turn_stream(session_id=session_id, input=turn_input):
            if event.type == "turn.done" and event.state.status != "done":
                raise pipeline.turn_error(event.state)
    pipeline.with_api_retry(consume, log=log)


def fetch_events(session_id: str) -> list[dict]:
    """All session events, oldest first, each with its turn_id."""
    items, token = [], None
    with httpx.Client(base_url=f"{BASE_URL}/api/v1", timeout=60) as http:
        while True:
            params = {"limit": 100, **({"page_token": token} if token else {})}
            page = http.get(f"/sessions/{session_id}/events", params=params).raise_for_status().json()
            items += page["data"]
            token = page["pagination"].get("next_page_token")
            if not token:
                break
    return [{**x["event"], "turn_id": x["turn_id"]} for x in reversed(items)]


def parse(content):
    if isinstance(content, str):
        try:
            return json.loads(content)
        except ValueError:
            return content
    return content


def state(session_id: str) -> dict:
    """Where the quote stands: pending gate, latest breakdown and PDF, what was actually sent."""
    events = fetch_events(session_id)
    calls = {tc["id"]: (tc["function"]["name"], tc["function"]["arguments"])
             for e in events if e["type"] == "model.message" for tc in e.get("tool_calls") or []}
    last_turn = events[-1]["turn_id"]
    done = next(e for e in reversed(events) if e["type"] == "turn.done")

    breakdown, quote, logs = None, None, []
    sent = {"quote": False, "counter_offer": False}
    for e in events:
        if e["type"] != "tool.response":
            continue
        name, args = calls.get(e["tool_call_id"], ("?", "{}"))
        result = parse(e.get("content"))
        breakdown = (pipeline.find_breakdowns(e.get("content")) or [breakdown])[-1]
        if name == "make_quote_pdf" and isinstance(result, dict) and "pdf_path" in result:
            quote = result
        if e["turn_id"] == last_turn:
            # Only a tool response saying "sent" counts; a denied call gets an error response instead.
            if name in ("send_quote", "send_counter_offer") and isinstance(result, dict) and result.get("status") == "sent":
                sent["quote" if name == "send_quote" else "counter_offer"] = True
            if name in SHOP_TOOLS:
                logs.append(f"{name} {'done' if not (isinstance(result, dict) and 'error' in result) else 'rejected'}")
            elif name == "exec":
                logs.append(f"sandbox: {parse(args).get('intent', 'ran code') if isinstance(parse(args), dict) else 'ran code'}")

    out = {"session_id": session_id, "breakdown": breakdown, "quote": quote, "sent": sent, "logs": logs, "message": None}
    st = done["state"]
    if st["status"] != "done":
        return {**out, "status": "error", "error": st.get("message") or st.get("reason")}
    pending = [a for a in st.get("required_actions") or [] if a["type"] == "tool.approval_required"]
    if pending:
        ref = pending[0]["tool_calls"][0]
        name, args = calls.get(ref["id"], ("?", "{}"))
        return {**out, "status": "gate", "gate": {"kind": GATES.get(name, "other"), "tool": name, "args": parse(args)}}
    return {**out, "status": "done", "message": (st.get("output") or {}).get("content")}


def start(req: dict) -> dict:
    enquiry = (req.get("enquiry") or "").strip()
    if not enquiry:
        return {"status": "error", "error": "enquiry is empty"}
    client = TrueForge(base_url=BASE_URL, timeout=600)
    _, result = pipeline.extract_and_validate(client, enquiry)
    base = {"spec": result.spec, "assumptions": result.assumptions}
    if not result.ok:
        return {**base, "status": "needs_clarification", "questions": result.questions, "reply": pipeline.clarification_reply(result)}
    session_id = client.sessions.create(agent={"name": pipeline.QUOTE_AGENT_NAME}).data.id
    run_turn(client, session_id, [{"type": "user.message", "content": pipeline.quote_message(enquiry, result)}])
    return {**base, **state(session_id)}


def decide(req: dict) -> dict:
    session_id, decision = req.get("session_id"), req.get("decision")
    if decision not in ("allow", "deny"):
        return {"status": "error", "error": "decision must be allow or deny"}
    current = state(session_id)
    if current["status"] != "gate":
        return {**current, "status": "error", "error": "no approval is pending for this session"}
    approval = {"status": "allow"} if decision == "allow" else {"status": "deny", "reason": req.get("reason") or "Owner rejected; keep as draft."}
    done = next(e for e in reversed(fetch_events(session_id)) if e["type"] == "turn.done")
    run_turn(TrueForge(base_url=BASE_URL, timeout=600), session_id, [
        {"type": "user.tool_approval", "thread_id": a["thread_id"], "tool_call_id": ref["id"], "approval": approval}
        for a in done["state"]["required_actions"] if a["type"] == "tool.approval_required" for ref in a["tool_calls"]
    ])
    return state(session_id)


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command not in ("start", "decide"):
        print(json.dumps({"status": "error", "error": "usage: api.py start|decide < request.json"}))
        return 2
    try:
        out = (start if command == "start" else decide)(json.load(sys.stdin))
    except Exception as e:  # the UI always gets JSON back
        out = {"status": "error", "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out))
    return 1 if out["status"] == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
