"""Register QuoteForge with a running TrueForge server. Safe to re-run (every call is an upsert).

Run: uv run setup.py
"""

import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

AGENT_NAME = "quoteforge"
TOOLS_SERVER = "quoteforge-tools"
COSTING_SKILL = "quoteforge-costing"
PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text()
NO_SANDBOX_NOTE = """
## No sandbox in this deployment

The costing skill is unavailable. Call `check_stock` with `kg_needed: null`, log that stock was checked without a required quantity, and do not produce quote numbers."""


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def model_name(model_id: str) -> str:
    # TrueForge resource names are lowercase slugs; the model id is sent upstream unchanged.
    return re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")[:64]


# MODEL_PROVIDER picks one; each reads its own key and model id from .env.
PROVIDERS = {
    "openai": {"key": "OPENAI_API_KEY", "model": "OPENAI_MODEL_ID", "base_url": None},
    "truefoundry": {"key": "TFY_API_KEY", "model": "TFY_MODEL_ID", "base_url": "TFY_GATEWAY_BASE_URL"},
}


def provider_manifest(client: httpx.Client, provider: str) -> tuple[dict, dict]:
    """Return the model provider manifest and the model properties for the configured provider."""
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        sys.exit(f"MODEL_PROVIDER must be one of {', '.join(PROVIDERS)}, got '{provider}'")
    missing = [v for v in (cfg["key"], cfg["model"], cfg["base_url"]) if v and not env(v)]
    if missing:
        sys.exit(f"Skipping model provider and agent: set {', '.join(missing)} in .env")

    model_id = env(cfg["model"])
    catalog = client.get("/catalogs/model-providers").json()["data"]
    known = [m for p in catalog if p["type"] == provider for m in p["models"] if m["model_id"] == model_id]
    properties = known[0]["properties"] if known else {}

    manifest = {
        "type": provider,
        "auth": {"api_key": env(cfg["key"])},
        "models": [{"model_id": model_id, "name": model_name(model_id), "properties": properties}],
    }
    if cfg["base_url"]:
        manifest["base_url"] = env(cfg["base_url"])
    return manifest, properties


def model_params(properties: dict) -> dict:
    # Reasoning models (GPT-5 family) reject temperature.
    if "low" in properties.get("reasoning_efforts", []):
        return {"reasoning_effort": "low"}
    return {"temperature": 0}


def build_spec(model_fqn: str, params: dict, sandbox: bool) -> dict:
    return {
        "model": {"name": model_fqn, "params": params},
        "instructions": PROMPT if sandbox else PROMPT + NO_SANDBOX_NOTE,
        # Skills run inside the sandbox, so they can only be attached when it is on.
        "skills": [{"name": COSTING_SKILL}] if sandbox else [],
        "mcp_servers": [
            {
                "name": TOOLS_SERVER,
                "enable_tools": ["@all"],
                # Named explicitly so the gate holds even if a server drops its annotations.
                "require_approval_for_tools": ["@destructive", "send_quote", "send_counter_offer", "create_po", "request_margin_approval"],
                "preload": True,
            }
        ],
        "config": {
            "sandbox": {"enabled": sandbox},
            "dynamic_sub_agents": {"enabled": False},
            "ask_user_questions": {"enabled": True},
            "iteration_limit": 30,
        },
    }


def put(client: httpx.Client, path: str, body: dict) -> dict:
    r = client.put(path, json=body)
    if r.is_error:
        sys.exit(f"PUT {path} failed ({r.status_code}): {r.text}")
    return r.json()


def main() -> None:
    client = httpx.Client(base_url=env("TRUEFORGE_BASE_URL", "http://localhost:8790") + "/api/v1", timeout=120)

    tools_url = env("TOOLS_MCP_URL", "http://127.0.0.1:8801/mcp")
    put(client, "/settings/mcp-servers", {"manifest": {
        "type": "remote",
        "name": TOOLS_SERVER,
        "url": tools_url,
        "description": "Shop rate card, stock check and quote sending.",
    }})
    print(f"MCP server '{TOOLS_SERVER}' -> {tools_url}")

    daytona_key = env("DAYTONA_API_KEY")
    if daytona_key:
        put(client, "/settings/sandbox-providers", {"manifest": {
            "type": "daytona",
            "auth": {"api_key": daytona_key},
            "exec_timeout_ms": 60000,
            "auto_stop_interval_in_minutes": 5,
            "auto_archive_interval_in_minutes": 60,
            "auto_delete_interval_in_minutes": 7200,
        }})
        # The skill is cloned from git into the sandbox, so sandbox/ changes need a push first.
        skill_ref = env("SKILL_REF", "main")
        put(client, "/settings/skills", {"manifest": {
            "type": "git",
            "name": COSTING_SKILL,
            "url": env("SKILL_REPO_URL", "https://github.com/aashu2006/quoteforge"),
            "path": "sandbox",
            "ref": skill_ref,
            "description": "Tested costing script for every quote number: weights, kg needed, full price breakdown with self-check.",
        }})
        print(f"Skill '{COSTING_SKILL}' @ {skill_ref}")
    print(f"Sandbox: {'on (Daytona)' if daytona_key else 'off (no DAYTONA_API_KEY)'}")

    provider = env("MODEL_PROVIDER", "openai")
    manifest, properties = provider_manifest(client, provider)
    put(client, "/settings/model-providers", {"manifest": manifest})
    model_fqn = f"{provider}/{manifest['models'][0]['name']}"
    params = model_params(properties)
    print(f"Model: {model_fqn} {params}")

    spec = build_spec(model_fqn, params, sandbox=bool(daytona_key))
    existing = next((a for a in client.get("/agents").json()["data"] if a["name"] == AGENT_NAME), None)
    if existing:
        put(client, f"/agents/{existing['id']}", {"manifest": spec})
    else:
        r = client.post("/agents", json={
            "name": AGENT_NAME,
            "description": "Turns fabrication enquiries into verified, owner-approved quotes.",
            "manifest": spec,
        })
        if r.is_error:
            sys.exit(f"POST /agents failed ({r.status_code}): {r.text}")
    print(f"Agent '{AGENT_NAME}' saved")


if __name__ == "__main__":
    main()
