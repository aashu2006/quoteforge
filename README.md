# quoteforge
AI agent that turns fabrication enquiries into verified, owner-approved quotes

## Run it

### Prerequisites

- Docker Desktop (running), for Postgres
- Node.js 22.14+ and npm, for TrueForge and the UI server
- [uv](https://docs.astral.sh/uv/), for the Python agent, tools and sandbox scripts
- An OpenAI API key (or TrueFoundry AI Gateway credentials)
- A Daytona API key with sandbox and snapshot permissions, and a default region set on its organization (needed for costing)

### Setup

```bash
git clone https://github.com/aashu2006/quoteforge.git
cd quoteforge
cp .env.example .env
```

Fill in `.env`. The variables are documented in [.env.example](.env.example):

| Variable | What to put |
| --- | --- |
| `MODEL_PROVIDER` | `openai` (default) or `truefoundry` |
| `OPENAI_API_KEY`, `OPENAI_MODEL_ID` | Your OpenAI key; model defaults to `gpt-5.5` |
| `TFY_GATEWAY_BASE_URL`, `TFY_API_KEY`, `TFY_MODEL_ID` | Only when `MODEL_PROVIDER=truefoundry` |
| `DAYTONA_API_KEY` | Turns on the sandbox where all costing runs |
| `POSTGRES_PASSWORD` | Any local password; the other `POSTGRES_*` defaults are fine |
| `TOOLS_MCP_URL` | `http://127.0.0.1:8802/mcp` (Postgres tools) or `http://127.0.0.1:8801/mcp` (mock fallback) |
| `SKILL_REF` | Git ref of `sandbox/` the sandbox clones; `demo-v1` is pinned for demos |

### Start

```bash
./start_all.sh
```

It starts, in order and each behind a health check: Postgres (Docker, seeded from `db/`), the shop tools MCP server (`tools/server.py` on :8802, or the mock on :8801), TrueForge (:8790), registers the agents, tools, sandbox and costing skill, then the UI server (:3000). Services already running are reused; logs go to `logs/`; Ctrl-C stops what the script started.

Open http://localhost:3000, pick a demo enquiry and press **Start quote**. Nothing is sent until you approve it at a gate.

### Check it

```bash
cd agent && uv run pytest tests -q                    # spec validation and retries
uv run --project agent pytest sandbox/tests -q        # costing and nesting (from the repo root)
cd tools && uv run pytest tests -q                    # tools contract vs the mock (needs both servers up)
cd agent && uv run run_demo.py --scenario 1           # CLI run of a demo scenario (1-6)
```

### Fall back to the mock tools

Set `TOOLS_MCP_URL=http://127.0.0.1:8801/mcp` in `.env` and re-run `./start_all.sh`. The mock has the same tools and contracts, with seed data in code instead of Postgres.

More detail on the agent side: [agent/README.md](agent/README.md). Design, contracts and scenarios: [docs/DESIGN.md](docs/DESIGN.md).
