"""Archive idle Daytona sandboxes so they stop counting against the org's disk quota.

Run: uv run cleanup_sandboxes.py
Each quote session gets its own sandbox (3 GiB); stopped ones keep using quota until archived.
Only `stopped` sandboxes are archived (Daytona auto-stops them after 5 idle minutes); `started`
ones may belong to a live session and are left alone. Archiving is reversible.
"""

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

API_URL = os.environ.get("DAYTONA_API_URL", "https://app.daytona.io/api")


def list_sandboxes(http: httpx.Client) -> list[dict]:
    sandboxes, cursor = [], None
    while True:
        params = {"limit": 100, **({"cursor": cursor} if cursor else {})}
        page = http.get("/sandbox", params=params).raise_for_status().json()
        sandboxes += page["items"]
        cursor = page.get("nextCursor")
        if not cursor:
            return sandboxes


def main() -> int:
    key = os.environ.get("DAYTONA_API_KEY", "").strip()
    if not key:
        print("DAYTONA_API_KEY not set; nothing to clean up.")
        return 0
    with httpx.Client(base_url=API_URL, headers={"Authorization": f"Bearer {key}"}, timeout=60) as http:
        sandboxes = list_sandboxes(http)
        # Skip ones already on their way to archived (archiving is asynchronous).
        stopped = [s for s in sandboxes if s["state"] == "stopped" and s.get("desiredState") != "archived"]
        archived, changing, failed = 0, 0, []
        for s in stopped:
            r = http.post(f"/sandbox/{s['id']}/archive")
            if r.is_success:
                archived += 1
            elif r.status_code == 409 or "not stopped" in r.text:
                changing += 1  # its state changed since listing (e.g. already archiving); nothing to do
            else:
                failed.append(f"{s['id']}: {r.status_code} {r.text[:200]}")
    active = sum(1 for s in sandboxes if s["state"] == "started")
    print(f"Archived {archived} stopped sandbox(es); {changing} already changing state; {active} running left alone.")
    for f in failed:
        print(f"  could not archive {f}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
