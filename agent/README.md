# agent/

QuoteForge agent on TrueForge. Needs Node 22.14+ and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env          # repo root; fill OPENAI_API_KEY (and DAYTONA_API_KEY to enable the sandbox)
cd agent
uv run mocks/server.py        # terminal 1: mock tools MCP server on :8801
./start_trueforge.sh          # terminal 2: TrueForge on :8790
uv run setup.py               # register tools, model, sandbox and the "quoteforge" agent (re-runnable)
uv run run_demo.py                 # scenario 1: stops at the send_quote approval gate
uv run run_demo.py --scenario 3    # target price below margin floor: stops at request_margin_approval
uv run run_demo.py --approve       # approve every gate in the run (or --deny)
uv run --project . pytest ../sandbox/tests   # costing and nesting tests
uv run warmup.py                   # before a live demo: warm a sandbox, then run_demo.py --session <id>
```

- New enquiries go through `pipeline.py`: the `quoteforge-extract` agent (no tools, strict JSON schema) transcribes the enquiry, `spec.py` validates it in code against `schemas/spec.schema.json`, and missing or invalid fields become customer questions before the quote agent starts. Scenario 2 (missing thickness) and 6 (Hinglish, mixed units) exercise this.
- Model API timeouts are retried up to 2 times per turn, logged as `API retry`.
- Model provider is `MODEL_PROVIDER` in `.env` (`openai` or `truefoundry`). To switch to the TrueFoundry gateway, set it to `truefoundry`, fill `TFY_*`, and re-run `setup.py`.
- Tools come from the MCP server at `TOOLS_MCP_URL`. To use the real tools, point it at that server and re-run `setup.py`.
- `send_quote`, `create_po` and `request_margin_approval` always need approval (annotated destructive, and named in `require_approval_for_tools`).
- The sandbox is on only when `DAYTONA_API_KEY` is set. Then `setup.py` registers `sandbox/` as the `quoteforge-costing` skill, cloned from `SKILL_REPO_URL` at `SKILL_REF`. Changes to `sandbox/` reach the agent only after they are pushed. Pin `SKILL_REF` to a tag for demos.
- Without the sandbox the agent checks stock with `kg_needed: null` and stops at a draft with no price.
- `start_trueforge.sh` allows `127.0.0.1` through TrueForge's outbound URL guard so it can reach the local tools server.
