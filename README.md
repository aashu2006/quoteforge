# QuoteForge

An AI agent that turns a fabrication enquiry into a checked quote, and waits for the owner's OK before anything goes out. Built on [TrueForge](https://trueforge.dev/introduction) for the "Build Agents That Act" hackathon.

**Demo video:** [watch here](https://drive.google.com/file/d/1sYHOn_OdXna8wse0ICQOZbzwhUaDvsX8/view?usp=sharing)

## Solution Writeup

**The problem.** Small fabrication shops in India price every job by hand: weight, rates, stock, labour, margin. One enquiry takes 1 to 2 hours. Math slips cost money and slow replies lose orders.

**What it does.** Paste a customer enquiry, even messy Hinglish with mixed units. The agent works out the spec, checks rates and stock, costs the job and makes a quotation PDF. It can negotiate price too. Nothing goes out without the owner's OK.

**What it reaches.** Postgres (rate card, stock, quotes) via our MCP tools server, a Daytona sandbox that runs the costing code, and a PDF generator.

**Where it stops.**

- Missing or unclear spec: it asks the customer, and the quote agent never starts.
- Margin under the 12% floor: the owner approves.
- Before any quote or counter-offer goes out: TrueForge Tool Approval.

**How it works.** Enquiry → extraction agent (strict JSON, no tools) → code checks the spec and converts units → quote agent on TrueForge → MCP tools and sandbox costing → approval gates → PDF. Customer replies go into the same session. For negotiation the agent writes its own script to compare options, but every price comes from our tested `costing.py`.

**TrueForge features used.** Agents and sessions, remote MCP tools, Tool Approval, a Daytona sandbox with a tag-pinned git skill, and strict `response_format` for extraction.

**Real vs mocked.** Real: Postgres, MCP tools, sandbox runs, approval gates, the PDF and the LLM (OpenAI). Mocked: rates, stock and customers are dummy data. Email is logged, not sent.

**Guardrails in code.** The model once made up its own rates despite the prompt. So the PDF tool re-runs costing with the shop's rate card and refuses on a mismatch. No PDF, no send.

**Known limits.** Rectangular parts only. No setup charge, so bigger quantities don't lower the unit price. Per-piece target prices are ignored. Clarification answers need a fresh enquiry. No real email yet. Local, single user.

## Run it

### What you need

- Docker Desktop, running (for Postgres)
- Node.js 22.14+ and npm (for TrueForge and the UI server)
- [uv](https://docs.astral.sh/uv/) (for the Python agent, tools and sandbox scripts)
- An OpenAI API key, or TrueFoundry AI Gateway credentials
- A Daytona API key with sandbox and snapshot permissions. Its organization needs a default region set, or the sandbox won't start and nothing gets costed.

### Setup

```bash
git clone https://github.com/aashu2006/quoteforge.git
cd quoteforge
cp .env.example .env
```

Fill in `.env`. Every variable is explained in [.env.example](.env.example). The main ones:

| Variable | What to put |
| --- | --- |
| `MODEL_PROVIDER` | `openai` (default) or `truefoundry` |
| `OPENAI_API_KEY`, `OPENAI_MODEL_ID` | Your OpenAI key; model defaults to `gpt-5.5` |
| `TFY_GATEWAY_BASE_URL`, `TFY_API_KEY`, `TFY_MODEL_ID` | Only when `MODEL_PROVIDER=truefoundry` |
| `DAYTONA_API_KEY` | Turns on the sandbox where all costing runs |
| `POSTGRES_PASSWORD` | Any local password; the other `POSTGRES_*` defaults are fine |
| `TOOLS_MCP_URL` | `http://127.0.0.1:8802/mcp` (Postgres tools) or `http://127.0.0.1:8801/mcp` (mock fallback) |
| `SKILL_REF` | Git ref of `sandbox/` the sandbox clones; `demo-v1` is pinned for demos |
| `PORT`, `API_TIMEOUT_MS`, `DAYTONA_API_URL` | Optional; the defaults are fine |

### Start

```bash
./start_all.sh
```

It starts everything in order and waits for each piece to be healthy:

1. Archives idle Daytona sandboxes, to free disk quota
2. Postgres in Docker, seeded from `db/`
3. The shop tools MCP server (`tools/server.py` on :8802, or the mock on :8801)
4. TrueForge on :8790, then registers the agents, tools, sandbox and costing skill
5. The UI server on :3000

Anything already running gets reused. Logs go to `logs/`. Ctrl-C stops only what the script started.

Open http://localhost:3000, pick a demo enquiry and press **Start quote**. Nothing goes out until you approve it.

Each quote gets its own Daytona sandbox (3 GiB). Stopped ones eat into the 30 GiB quota until they're archived. `start_all.sh` cleans them up first. If the stack is already up, run `cd agent && uv run cleanup_sandboxes.py` before a demo.

### Check it

From the repo root:

```bash
(cd agent && uv run pytest tests -q)              # spec checks, API bridge, retries
uv run --project agent pytest sandbox/tests -q    # costing and nesting
(cd tools && uv run pytest tests -q)              # tools vs the mock (both servers must be up)
(cd agent && uv run run_demo.py --scenario 1)     # one demo scenario from the CLI (1-6)
```

### Fall back to the mock tools

Set `TOOLS_MCP_URL=http://127.0.0.1:8801/mcp` in `.env` and run `./start_all.sh` again. The mock has the same tools and contracts. Its seed data lives in code instead of Postgres.

More on the agent: [agent/README.md](agent/README.md). Design, contracts and scenarios: [docs/DESIGN.md](docs/DESIGN.md).

## Team

Akshat Patil ([@aashu2006](https://github.com/aashu2006)) and Famous ([@Famous077](https://github.com/Famous077))
