import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api  # noqa: E402
import pipeline  # noqa: E402


def test_failed_turn_still_reports_what_was_sent(monkeypatch):
    # The owner approved, send_quote ran, then the model call timed out: the UI must still learn it was sent.
    def failing_turn(*args, **kwargs):
        raise pipeline.TurnError("Turn ended with status error: Cannot connect to API: Connect Timeout Error")

    after = {"status": "error", "session_id": "s1", "sent": {"quote": True, "counter_offer": False}, "error": "timeout"}
    monkeypatch.setattr(api, "run_turn", failing_turn)
    monkeypatch.setattr(api, "state", lambda session_id: after)
    assert api.run_turn_then_state(None, "s1", [], retry=False) == after


def test_decide_does_not_retry_approvals(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "state", lambda session_id: {"status": "gate", "session_id": session_id})
    monkeypatch.setattr(api, "fetch_events", lambda session_id: [{"type": "turn.done", "state": {"required_actions": [
        {"type": "tool.approval_required", "thread_id": "main", "tool_calls": [{"id": "call-1"}]}]}}])
    monkeypatch.setattr(api, "run_turn", lambda client, sid, turn_input, retry=True: calls.append(retry))
    monkeypatch.setattr(api, "TrueForge", lambda **kwargs: None)
    api.decide({"session_id": "s1", "decision": "allow"})
    assert calls == [False]


def test_reply_refuses_while_a_gate_is_pending(monkeypatch):
    monkeypatch.setattr(api, "state", lambda session_id: {"status": "gate", "session_id": session_id})
    out = api.reply({"session_id": "s1", "message": "Can you do it for Rs 7,500?"})
    assert out["status"] == "error" and "pending approval" in out["error"]


def test_reply_needs_a_message():
    assert api.reply({"session_id": "s1", "message": "  "})["status"] == "error"
