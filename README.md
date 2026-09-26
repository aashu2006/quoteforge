# QuoteForge

AI agent that turns fabrication enquiries into verified, owner-approved quotes. Built on [TrueForge](https://trueforge.dev/introduction) for the "Build Agents That Act" hackathon.

## Solution Writeup

**Problem:** Small fabrication shops in India quote every job by hand: weight, rates, stock, labour, margin. It takes 1-2 hours per enquiry, math errors cost money and slow replies lose orders.

**What the agent does:** Turns a customer enquiry (even Hinglish with mixed units) into a verified quotation PDF, handles price negotiation, and never sends anything without the owner's approval.

**What it reaches:** Postgres (rate card, stock, quotes) through our MCP tools server, a Daytona sandbox where costing code runs, and a PDF generator.

**Where it stops:**

- Missing or ambiguous specs: asks the customer, the quote agent never starts
- Margin below the 12% floor: owner approval
- Before sending any quote or counter-offer: TrueForge Tool Approval

**Architecture:** Enquiry → extraction agent (strict JSON, no tools) → code validates the spec and converts units → quote agent on TrueForge → MCP tools + sandbox costing → approval gates → PDF. Customer replies continue in the same session; for negotiation the agent writes its own script to compare options, but every price comes from the tested `costing.py`.

**How TrueForge was used:** agents and sessions, remote MCP tools, Tool Approval on destructive tools, Daytona sandbox with a git skill pinned to a tag, strict `response_format` for extraction.

**Real vs mocked:** Real: Postgres, MCP tools, sandbox execution, approval gates, PDF, LLM (OpenAI). Mocked: rates, stock and customers are dummy data; email is logged, not sent.

**Guardrails in code:** The model once invented its own rates despite the prompt. So the PDF tool re-runs costing with the shop's rate card and refuses on mismatch, and nothing can be sent without a PDF.

**Known limits:** Rectangular parts only; quantity doesn't lower unit price (no setup charge modelled); per-piece target prices are ignored; clarification answers need a resubmitted enquiry; no real email yet; runs locally, single user.

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

Each quote gets its own Daytona sandbox, and stopped ones count against the 30 GiB disk quota until archived. `start_all.sh` archives them first; run `cd agent && uv run cleanup_sandboxes.py` before a demo if the stack is already up.

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

## Team

Akshat Patil ([@aashu2006](https://github.com/aashu2006)) and Famous ([@Famous077](https://github.com/Famous077))