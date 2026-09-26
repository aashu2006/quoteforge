import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import API_RETRIES, TurnError, with_api_retry  # noqa: E402

TIMEOUT = "Turn ended with status error: Cannot connect to API: Connect Timeout Error (timeout: 10000ms)"


def flaky(failures: list[str]):
    calls = {"n": 0}

    def run():
        calls["n"] += 1
        if failures:
            raise TurnError(failures.pop(0))
        return "ok"
    return run, calls


def test_retries_timeouts_and_logs():
    run, calls = flaky([TIMEOUT, TIMEOUT])
    logs = []
    assert with_api_retry(run, log=logs.append) == "ok"
    assert calls["n"] == 3 and len(logs) == 2 and logs[0].startswith("API retry 1/2")


def test_gives_up_after_retries():
    run, calls = flaky([TIMEOUT] * (API_RETRIES + 1))
    with pytest.raises(TurnError):
        with_api_retry(run, log=lambda _: None)
    assert calls["n"] == API_RETRIES + 1


def test_non_transient_error_is_not_retried():
    run, calls = flaky(["Turn ended with status error: invalid tool arguments"])
    with pytest.raises(TurnError):
        with_api_retry(run, log=lambda _: None)
    assert calls["n"] == 1
