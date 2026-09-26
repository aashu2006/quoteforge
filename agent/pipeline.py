"""New-enquiry pipeline: extract -> validate in code -> clarify, or hand a validated spec to the quote agent.

The extraction agent has no tools, so an incomplete enquiry stops here with zero shop tool calls.
Negotiation replies go straight to the quote agent's session and do not pass through here.
"""

import json
import re
from typing import Callable, TypeVar

import httpx
from trueforge_sdk import TrueForge

from spec import Result, validate

EXTRACT_AGENT_NAME = "quoteforge-extract"
QUOTE_AGENT_NAME = "quoteforge"
API_RETRIES = 2
TRANSIENT = re.compile(r"timeout|timed out|cannot connect|econnreset|socket hang up|overloaded|rate limit|\b50[234]\b", re.I)

T = TypeVar("T")


class TurnError(RuntimeError):
    """A turn ended in error. `transient` marks model API/network failures worth retrying."""

    def __init__(self, message: str):
        super().__init__(message)
        self.transient = bool(TRANSIENT.search(message))


def turn_error(state) -> TurnError:
    detail = getattr(state, "message", None) or getattr(state, "reason", None)
    return TurnError(f"Turn ended with status {state.status}: {detail}")


def with_api_retry(run: Callable[[], T], log: Callable[[str], None] = print) -> T:
    """Retry a turn up to API_RETRIES times on model API timeouts or dropped connections."""
    for attempt in range(API_RETRIES + 1):
        try:
            return run()
        except (TurnError, httpx.TransportError) as e:
            transient = getattr(e, "transient", True)
            if not transient or attempt == API_RETRIES:
                raise
            log(f"API retry {attempt + 1}/{API_RETRIES}: {e}")
    raise AssertionError("unreachable")


def extract(client: TrueForge, enquiry: str) -> str:
    """Run the extraction agent and return its raw JSON text (validated by the caller, never trusted)."""
    def run() -> str:
        # Fresh session per attempt so a failed attempt leaves no half-finished history behind.
        session_id = client.sessions.create(agent={"name": EXTRACT_AGENT_NAME}).data.id
        output = ""
        for event in client.sessions.create_turn_stream(session_id=session_id, input=[{"type": "user.message", "content": enquiry}]):
            if event.type == "turn.done":
                if event.state.status != "done":
                    raise turn_error(event.state)
                output = (event.state.output.content or "") if event.state.output else ""
        return output
    return with_api_retry(run)


def extract_and_validate(client: TrueForge, enquiry: str) -> tuple[str, Result]:
    raw = extract(client, enquiry)
    return raw, validate(raw)


def quote_message(enquiry: str, result: Result, note: str = "") -> str:
    """First message to the quote agent: the enquiry for context, plus the spec it must use as-is."""
    assumptions = "\n".join(f"- {a}" for a in result.assumptions) or "- none"
    return (
        f"New enquiry:\n{enquiry}\n\n"
        f"Validated spec (checked in code; use it exactly, do not re-extract or change it):\n"
        f"```json\n{json.dumps(result.spec, indent=2)}\n```\n\n"
        f"Assumptions already made while validating (log each as `Assumption:`):\n{assumptions}"
        + (f"\n\n{note}" if note else "")
    )


def clarification_reply(result: Result) -> str:
    return "Thank you for your enquiry. Before we can quote, could you please help us with the following?\n\n" + \
        "\n".join(f"{i}. {q}" for i, q in enumerate(result.questions, 1))


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
