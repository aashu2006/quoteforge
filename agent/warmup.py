"""Warm the sandbox before a live demo: provision it, load the costing skill and run the script once.

Run: uv run warmup.py
A sandbox belongs to one session, so pass the printed session id to run_demo.py --session.
"""

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from trueforge_sdk import TrueForge

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

AGENT_NAME = "quoteforge"
WARMUP = """Sandbox warmup, not a customer enquiry. Do not call any shop tools.
Load the quoteforge-costing skill, then run `python3 costing.py --weight-only < examples/scenario1.json` in the skill's directory.
Reply with only the kg_needed value from the output."""


def main() -> None:
    if not os.environ.get("DAYTONA_API_KEY"):
        sys.exit("DAYTONA_API_KEY is not set, so the agent has no sandbox to warm.")

    client = TrueForge(base_url=os.environ.get("TRUEFORGE_BASE_URL") or "http://localhost:8790", timeout=600)
    session_id = client.sessions.create(agent={"name": AGENT_NAME}).data.id
    print(f"Session {session_id}: warming sandbox...")

    start = time.monotonic()
    output, execs = "", 0
    for event in client.sessions.create_turn_stream(session_id=session_id, input=[{"type": "user.message", "content": WARMUP}]):
        if event.type == "sandbox.created":
            print(f"  sandbox created after {time.monotonic() - start:.1f}s")
        elif event.type == "tool.response":
            execs += 1
        elif event.type == "turn.done":
            if event.state.status != "done":
                sys.exit(f"Warmup failed: {getattr(event.state, 'message', None) or getattr(event.state, 'reason', None)}")
            output = (event.state.output.content or "") if event.state.output else ""

    ok = "65.94" in output
    print(f"  {execs} sandbox call(s), {time.monotonic() - start:.1f}s total, agent replied: {output.strip()[:200]!r}")
    if not ok:
        sys.exit("Warmup ran but costing.py did not return the expected kg_needed (65.94).")
    print(f"\nReady. Run the demo in this warmed session:\n  uv run run_demo.py --session {session_id} [--scenario 1|3|4|5] [--approve | --deny]")


if __name__ == "__main__":
    main()
