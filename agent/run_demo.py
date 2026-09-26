"""Run a demo scenario through the saved QuoteForge agent and stop at the first approval gate.

Run: uv run run_demo.py [--scenario 1-6] [--approve | --deny] [--session ID]
Without a decision flag the run stops at the first gate and nothing is sent.
With one, every gate in the run gets that decision.
Scenarios 4 and 5 first send the scenario 1 quote (its send gate is allowed as setup), then the customer replies.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from trueforge_sdk import TrueForge
from trueforge_sdk.events import is_event_delta, merge_event_delta

import pipeline

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

AGENT_NAME = "quoteforge"

# Moves to demo/ once it lands there. Scenario 3 is scenario 1 with a target that puts margin at 8.14%.
SCENARIOS = {
    1: """From: Rakesh Sharma, Sharma Industries <purchase@sharma-industries.example>

Hi, please quote for 50 nos MS L brackets, 200 x 100 x 8 mm, 1 bend and 2 holes each, powder coated.
Delivery needed in 2 weeks.""",
    2: """From: Rakesh Sharma, Sharma Industries <purchase@sharma-industries.example>

Hi, please quote for 50 nos MS L brackets, 200 x 100 mm, 1 bend and 2 holes each, powder coated.
Delivery needed in 2 weeks.""",
    3: """From: Rakesh Sharma, Sharma Industries <purchase@sharma-industries.example>

Hi, please quote for 50 nos MS L brackets, 200 x 100 x 8 mm, 1 bend and 2 holes each, powder coated.
Our budget is Rs 8,700 for the full order, all inclusive of GST. Delivery needed in 2 weeks.""",
    # Messy Hinglish with mixed units; must give the same spec as scenario 1.
    6: """From: Rakesh Sharma, Sharma Industries <purchase@sharma-industries.example>

bhai 50 pcs L bracket chahiye, MS, 20cm x 10cm, 8mm plate, powder coating karke, 2 hole""",
}
S1_ITEMS = json.loads((Path(__file__).resolve().parents[1] / "sandbox" / "examples" / "scenario1.json").read_text())["spec"]["items"]
THICKNESS_QUESTION = "Could you please confirm the plate thickness of the L bracket (in mm)?"
# Negotiation: the customer replies to the scenario 1 quote (Rs 9,654.44; floor at 12% is Rs 9,010.81).
REPLIES = {
    4: "Thanks for the quote. Can you do it for Rs 7,500?",  # below floor, even below cost: counter-offer
    5: "Thanks for the quote. Can you do it for Rs 9,200?",  # above floor: revised quote at target
}
EXPECTED_FIRST_GATE = {1: "send_quote", 3: "request_margin_approval", 4: "send_counter_offer", 5: "send_quote", 6: "send_quote"}
FLOOR_TOTAL = 9010.81



def stream_turn(client: TrueForge, session_id: str, turn_input: list, events: dict) -> list:
    """Stream one turn, retrying on model API timeouts, and return pending approval events."""
    return pipeline.with_api_retry(lambda: _stream_turn(client, session_id, turn_input, events))


def _stream_turn(client: TrueForge, session_id: str, turn_input: list, events: dict) -> list:
    """Stream one turn, print tool activity and costing breakdowns, and return pending approval events."""
    pending = []
    for event in client.sessions.create_turn_stream(session_id=session_id, input=turn_input):
        if is_event_delta(event):
            base = events.get(event.id)
            if base is not None:
                merge_event_delta(base, event)
            continue
        events[event.id] = event

        if event.type == "tool.response":
            name, args = find_call(events, event.tool_call_id)
            breakdowns = find_breakdowns(event.content)
            if breakdowns:
                print(f"\n-> {name}(...)")
                for breakdown in breakdowns:
                    print_breakdown(breakdown)
            else:
                print(f"\n-> {name}({args})\n<- {str(event.content)[:800]}")
        elif event.type == "tool.approval_required":
            pending.append(event)
        elif event.type == "tool.response_required":
            for ref in event.tool_calls:
                print(f"\nAgent asked: {find_call(events, ref.id)[1]}")
        elif event.type == "turn.done":
            state = event.state
            if state.status != "done":
                raise pipeline.turn_error(state)
            if state.output is not None and state.output.content:
                print(f"\nAgent:\n{state.output.content}")
    return pending


def find_call(events: dict, tool_call_id: str) -> tuple[str, str]:
    for e in events.values():
        if e.type == "model.message":
            for tc in e.tool_calls or []:
                if tc.id == tool_call_id:
                    return tc.function.name, tc.function.arguments
    return "?", "?"


def find_breakdowns(content) -> list[dict]:
    """Pull every costing.py breakdown out of a sandbox tool response, however the harness wraps stdout.

    One command can run costing.py more than once (e.g. --at-floor then --price-at-target).
    """
    if isinstance(content, dict):
        if "line_items" in json.dumps(content) and "total" in content:
            return [content]
        return [b for v in content.values() for b in find_breakdowns(v)]
    if isinstance(content, list):
        return [b for v in content for b in find_breakdowns(v)]
    found = []
    if isinstance(content, str) and "line_items" in content:
        decoder = json.JSONDecoder()
        i = 0
        while (i := content.find("{", i)) != -1:
            try:
                obj, end = decoder.raw_decode(content, i)
            except ValueError:
                i += 1
                continue
            found += find_breakdowns(obj)
            i = end
    return found


def print_breakdown(b: dict) -> None:
    rs = lambda x: f"{x:>12,.2f}"
    print("   === Costing breakdown (costing.py) ===")
    for item in b["items"]:
        dims = " x ".join(str(d) for d in item["dimensions_mm"])
        print(f"   {item['name']} x {item['qty']} ({item['material']} {dims} mm), {item['weight_kg_per_piece']} kg/pc, needs {item['kg_needed']} kg")
        for line in item["line_items"]:
            print(f"     {line['label']:<10} {line['detail']:<48} {rs(line['amount'])}")
    print(f"   {'Cost subtotal':<61} {rs(b['cost_subtotal'])}")
    print(f"   {'Overhead ' + str(b['overhead']['pct']) + '%':<61} {rs(b['overhead']['amount'])}")
    print(f"   {'Margin ' + str(b['margin']['pct']) + '%':<61} {rs(b['margin']['amount'])}")
    print(f"   {'Price before GST':<61} {rs(b['price_before_gst'])}")
    print(f"   {'GST ' + str(b['gst']['pct']) + '%':<61} {rs(b['gst']['amount'])}")
    print(f"   {'TOTAL':<61} {rs(b['total'])}")
    mc, sc = b["margin_check"], b["self_check"]
    effective = "n/a (no target price)" if mc["effective_margin_pct"] is None else f"{mc['effective_margin_pct']}%"
    print(f"   Margin check: effective {effective} vs floor {mc['margin_floor_pct']}%, below floor: {mc['below_floor']}")
    print(f"   Self-check: {'passed' if sc['passed'] else 'FAILED ' + '; '.join(sc['errors'])}")


def gates(events: dict, pending: list) -> list[tuple]:
    return [(p.thread_id, ref.id, *find_call(events, ref.id)) for p in pending for ref in p.tool_calls]


def print_gate(name: str, call_args: str) -> None:
    if name != "send_counter_offer":
        print(f"PAUSED before {name}({call_args})")
        return
    offer = json.loads(call_args)
    print(f"PAUSED before send_counter_offer to {offer['email']} (quote {offer['quote_id']})\n\n   Message:")
    print("\n".join(f"   | {line}" for line in offer["message"].splitlines()))
    print("\n   Options:")
    for o in offer["options"]:
        print(f"     {o['label']:<34} {o['changes']:<44} Rs {o['total']:>10,.2f}  (Rs {o['unit_price']:,.2f}/pc before GST)")


def resolve_gates(client: TrueForge, session_id: str, pending_gates: list, approval: dict, events: dict) -> None:
    while pending_gates:
        print(f"\nOwner decision on {', '.join(g[2] for g in pending_gates)}: {approval['status']}")
        pending = stream_turn(client, session_id, [
            {"type": "user.tool_approval", "thread_id": thread_id, "tool_call_id": call_id, "approval": approval}
            for thread_id, call_id, _, _ in pending_gates
        ], events)
        pending_gates = gates(events, pending)
        for _, _, name, call_args in pending_gates:
            print()
            print_gate(name, call_args)


def report(title: str, checks: dict) -> bool:
    print(f"\n=== {title} ===")
    for label, passed in checks.items():
        print(f"  [{'x' if passed else ' '}] {label}")
    return all(checks.values())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", type=int, choices=sorted([*SCENARIOS, *REPLIES]), default=1)
    decision = parser.add_mutually_exclusive_group()
    decision.add_argument("--approve", action="store_true", help="allow every gated call")
    decision.add_argument("--deny", action="store_true", help="deny every gated call")
    parser.add_argument("--session", help="reuse a session (e.g. one warmed by warmup.py) instead of opening a new one")
    args = parser.parse_args()

    sandbox = bool(os.environ.get("DAYTONA_API_KEY"))
    negotiation = args.scenario in REPLIES
    if negotiation and not sandbox:
        sys.exit("Negotiation scenarios need the sandbox (DAYTONA_API_KEY).")

    client = TrueForge(base_url=os.environ.get("TRUEFORGE_BASE_URL") or "http://localhost:8790", timeout=600)
    enquiry = SCENARIOS[1 if negotiation else args.scenario]
    print(f"Scenario {args.scenario} enquiry:\n{enquiry}")

    # Extraction and validation run before the quote agent exists, so an incomplete spec reaches no tool.
    raw, result = pipeline.extract_and_validate(client, enquiry)
    print(f"\n=== Extraction (raw, untrusted) ===\n{raw}\n\n=== Validated in code ===")
    print(json.dumps(result.spec, indent=2))
    for a in result.assumptions:
        print(f"Assumption: {a}")
    if not result.ok:
        print(f"\n=== Clarification for the customer ===\n{pipeline.clarification_reply(result)}")
        sys.exit(0 if clarification_checks(args.scenario, result) else 1)

    session_id = args.session or client.sessions.create(agent={"name": AGENT_NAME}).data.id
    print(f"\nSession {session_id}")
    events: dict = {}
    pending = stream_turn(client, session_id, [{"type": "user.message", "content": pipeline.quote_message(enquiry, result)}], events)

    if negotiation:
        setup = gates(events, pending)
        if not setup or setup[0][2] != "send_quote":
            sys.exit(f"Setup failed: expected the scenario 1 quote to pause at send_quote, got {[g[2] for g in setup]}")
        print("\n=== Setup: owner approves the standard scenario 1 quote ===")
        resolve_gates(client, session_id, setup, {"status": "allow"}, events)
        print(f"\nCustomer replies: {REPLIES[args.scenario]}")
        mark = len(events)
        pending = stream_turn(client, session_id, [{"type": "user.message", "content": REPLIES[args.scenario]}], events)
        turn = list(events.values())[mark:]
    else:
        turn = list(events.values())
    first = gates(events, pending)

    responses = [e for e in turn if e.type == "tool.response"]
    called = [find_call(events, e.tool_call_id)[0] for e in responses]

    print("\n=== Approval gate ===")
    if not first:
        print("No approval pending.")
    for _, _, name, call_args in first:
        print_gate(name, call_args)

    checks = {"nothing sent": not {"send_quote", "send_counter_offer"} & set(called)}
    if not sandbox:
        # Without costing there are no trusted numbers, so the agent keeps a draft and stops.
        checks["stops as draft without a gate (no sandbox)"] = not first
    else:
        checks[f"first gate is {EXPECTED_FIRST_GATE[args.scenario]}"] = bool(first) and first[0][2] == EXPECTED_FIRST_GATE[args.scenario]
    if negotiation:
        checks.update(negotiation_checks(events, turn, first, args.scenario))
    else:
        checks.update(quote_checks(events, responses, called, sandbox, args.scenario))
    if args.scenario == 6:
        checks["extracted spec equals scenario 1 (200 x 100 x 8 mm, same ops and finish)"] = result.spec["items"] == S1_ITEMS
    ok = report(f"Scenario {args.scenario} checks", checks)
    print(f"\nScenario {args.scenario}: {'PASS' if ok else 'FAIL'} (tools run: {called})")

    if args.approve or args.deny:
        approval = {"status": "allow"} if args.approve else {"status": "deny", "reason": "Owner rejected; keep as draft."}
        resolve_gates(client, session_id, first, approval, events)
        if sandbox and args.scenario == 3:
            ok = check_scenario3_outcome(events, approved=args.approve) and ok
        if args.scenario == 4:
            ok = check_counter_offer_outcome(events, turn_start=mark, approved=args.approve) and ok

    sys.exit(0 if ok else 1)


def clarification_checks(scenario: int, result) -> bool:
    checks = {"stopped before the quote agent started (0 shop tool calls)": True}  # this path never opens a quote session
    if scenario == 2:
        checks["missing is exactly thickness"] = result.spec["missing"] == ["items[0].thickness_mm"]
        checks["one thickness question for the customer"] = result.questions == [THICKNESS_QUESTION]
    else:
        checks[f"scenario {scenario} was expected to reach the quote agent"] = False
    ok = report(f"Scenario {scenario} checks", checks)
    print(f"\nScenario {scenario}: {'PASS' if ok else 'FAIL'}")
    return ok


def quote_checks(events: dict, responses: list, called: list, sandbox: bool, scenario: int) -> dict:
    checks = {"rate card fetched": "get_rate_card" in called, "stock checked": "check_stock" in called}
    if sandbox:
        stock_kg = [json.loads(find_call(events, e.tool_call_id)[1]).get("kg_needed") for e in responses if find_call(events, e.tool_call_id)[0] == "check_stock"]
        breakdowns = [b for e in responses for b in find_breakdowns(e.content)]
        checks["check_stock got kg_needed 65.94"] = 65.94 in stock_kg
        checks["costing.py breakdown total 9654.44"] = any(b["total"] == 9654.44 and b["self_check"]["passed"] for b in breakdowns)
        pdfs = [json.loads(str(e.content)) for e in responses if find_call(events, e.tool_call_id)[0] == "make_quote_pdf" and "pdf_path" in str(e.content)]
        checks["quote PDF made before the send gate"] = scenario == 3 or bool(pdfs)
        if scenario == 3:
            checks["margin below floor (8.14%)"] = any(b["margin_check"]["effective_margin_pct"] == 8.14 for b in breakdowns)
    return checks


def sandbox_numbers(events: dict, turn: list) -> set[float]:
    """Every number printed by sandbox commands in this turn: offered prices must be among them."""
    numbers = set()
    for e in turn:
        if e.type == "tool.response" and find_call(events, e.tool_call_id)[0] == "exec":
            numbers |= {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*\.\d+|\d[\d,]*", str(e.content))}
    return numbers


def negotiation_checks(events: dict, turn: list, first: list, scenario: int) -> dict:
    exec_cmds = [json.loads(args).get("command", "") for e in turn if e.type == "model.message"
                 for tc in e.tool_calls or [] if tc.function.name == "exec" and (args := tc.function.arguments)]
    numbers = sandbox_numbers(events, turn)
    materials = set(re.findall(r"""["']material["']\s*:\s*["'](\w+)""", "\n".join(exec_cmds)))
    thicknesses = {float(t) for t in re.findall(r"""["']thickness_mm["']\s*:\s*([\d.]+)""", "\n".join(exec_cmds))}
    checks = {
        f"floor price {FLOOR_TOTAL:,.2f} computed by costing.py --at-floor": any("--at-floor" in c for c in exec_cmds) and FLOOR_TOTAL in numbers,
        "material and thickness unchanged (MS, 8 mm)": materials <= {"MS"} and thicknesses <= {8.0},
    }
    if scenario == 5:
        at_target = [b for e in turn if e.type == "tool.response" for b in find_breakdowns(e.content) if b.get("priced_at_target")]
        checks["revised quote priced at target: total 9200.00"] = any(b["total"] == 9200 and b["self_check"]["passed"] for b in at_target)
        return checks

    offer = json.loads(first[0][3]) if first and first[0][2] == "send_counter_offer" else {"options": [], "message": ""}
    totals = [round(o["total"], 2) for o in offer["options"]]
    changes = " ".join(o["changes"] for o in offer["options"]).lower()
    checks.update({
        "agent wrote its own options script running costing.py": any(
            "costing.py" in c and ("subprocess" in c or c.count("costing.py") > 1 or "for " in c) for c in exec_cmds),
        f"2-3 options offered ({len(totals)})": 2 <= len(totals) <= 3,
        "every option total printed by a sandbox run": bool(totals) and all(t in numbers for t in totals),
        "no option changes material or thickness": not re.search(r"ss304|stainless|alumin|thickness|\b(?!8\b)\d+\s*mm", changes),
        "counter-offer message drafted": len(offer["message"].strip()) > 0,
    })
    return checks


def check_counter_offer_outcome(events: dict, turn_start: int, approved: bool) -> bool:
    responses = [e for e in list(events.values())[turn_start:] if e.type == "tool.response"]
    sent = any(find_call(events, e.tool_call_id)[0] == "send_counter_offer" and '"sent"' in str(e.content) for e in responses)
    quoted = any(find_call(events, e.tool_call_id)[0] == "send_quote" for e in responses)
    if approved:
        return report("Outcome", {"counter-offer sent": sent, "no revised quote sent": not quoted})
    return report("Outcome", {"counter-offer not sent": not sent, "no revised quote sent": not quoted})


def check_scenario3_outcome(events: dict, approved: bool) -> bool:
    """After the gates: approve must send the quote priced at target; deny must keep the standard draft."""
    responses = [e for e in events.values() if e.type == "tool.response"]
    sent_at = next((i for i, e in enumerate(responses) if find_call(events, e.tool_call_id)[0] == "send_quote" and "sent" in str(e.content)), None)
    at_target = [(i, b) for i, e in enumerate(responses) for b in find_breakdowns(e.content) if b.get("priced_at_target")]

    if approved:
        before_send = [b for i, b in at_target if sent_at is not None and i < sent_at]
        checks = {
            "quote sent": sent_at is not None,
            "sent quote priced at target: total 8700.00, margin 8.14%": any(
                b["total"] == 8700 and b["margin"]["pct"] == 8.14 and b["self_check"]["passed"] for b in before_send),
        }
    else:
        checks = {"quote not sent": sent_at is None, "not priced at target (draft at standard price)": not at_target}
    print("\n=== Outcome ===")
    for label, passed in checks.items():
        print(f"  [{'x' if passed else ' '}] {label}")
    return all(checks.values())


if __name__ == "__main__":
    try:
        main()
    except pipeline.TurnError as e:
        sys.exit(str(e))
