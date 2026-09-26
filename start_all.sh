#!/usr/bin/env bash
# Start QuoteForge locally, in order, each behind a health check:
#   Postgres (Docker) -> shop tools MCP server -> TrueForge -> agent registration -> Node UI server.
# Services already running are reused. Logs go to logs/. Ctrl-C stops what this script started.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"
STARTED=()

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

cleanup() {
  if [ ${#STARTED[@]} -gt 0 ]; then
    say "Stopping services started by this script..."
    kill "${STARTED[@]}" 2>/dev/null || true
  fi
}
trap cleanup INT TERM

# wait_for <name> <timeout_s> <command...>: poll until the command succeeds.
wait_for() {
  local name=$1 timeout=$2; shift 2
  for ((i = 0; i < timeout; i++)); do
    if "$@" >/dev/null 2>&1; then echo "  $name is up"; return 0; fi
    sleep 1
  done
  fail "$name did not come up within ${timeout}s (see $LOGS/)"
}

# Any listener on the port, IPv4 or IPv6 (TrueForge binds only [::1]).
port_open() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null; }

# start <name> <port> <logfile> <dir> <command...>: run in the background unless the port is already served.
start() {
  local name=$1 port=$2 log=$3 dir=$4; shift 4
  if port_open "$port"; then echo "  $name already running on :$port, reusing it"; return; fi
  (cd "$dir" && exec "$@") >"$LOGS/$log" 2>&1 &
  STARTED+=($!)
}

say "0. Checking prerequisites"
[ -f "$ROOT/.env" ] || fail ".env not found. Run: cp .env.example .env and fill it in."
set -a; source "$ROOT/.env"; set +a
for var in POSTGRES_PASSWORD TOOLS_MCP_URL; do
  [ -n "${!var:-}" ] || fail "$var is empty in .env"
done
case "${MODEL_PROVIDER:-openai}" in
  openai)      [ -n "${OPENAI_API_KEY:-}" ] || fail "OPENAI_API_KEY is empty in .env" ;;
  truefoundry) [ -n "${TFY_API_KEY:-}" ] || fail "TFY_API_KEY is empty in .env" ;;
esac
for cmd in docker uv node npm npx lsof curl; do
  command -v "$cmd" >/dev/null || fail "$cmd is not installed"
done
docker info >/dev/null 2>&1 || fail "Docker is not running. Start Docker Desktop first."
[ -n "${DAYTONA_API_KEY:-}" ] || echo "  Note: DAYTONA_API_KEY is empty, so the agent runs without a sandbox and cannot price quotes."

if [ -n "${DAYTONA_API_KEY:-}" ]; then
  say "0b. Archiving idle Daytona sandboxes (frees the disk quota)"
  (cd "$ROOT/agent" && uv run cleanup_sandboxes.py) || echo "  Warning: sandbox cleanup failed; continuing."
fi

say "1. Postgres (Docker)"
docker compose -f "$ROOT/db/docker-compose.yml" --env-file "$ROOT/.env" up -d >"$LOGS/postgres.log" 2>&1
wait_for Postgres 60 docker compose -f "$ROOT/db/docker-compose.yml" --env-file "$ROOT/.env" exec -T db pg_isready -U "${POSTGRES_USER:-quoteforge}"

say "2. Shop tools MCP server"
TOOLS_PORT=$(echo "$TOOLS_MCP_URL" | sed -E 's#.*:([0-9]+)/.*#\1#')
if [ "$TOOLS_PORT" = "8801" ]; then
  echo "  TOOLS_MCP_URL points at the mock (fallback mode)"
  start "Mock tools" 8801 tools-mock.log "$ROOT/agent" uv run mocks/server.py --port 8801
else
  start "Tools server" "$TOOLS_PORT" tools.log "$ROOT/tools" uv run server.py --port "$TOOLS_PORT"
fi
wait_for "Tools server (:$TOOLS_PORT)" 60 port_open "$TOOLS_PORT"

say "3. TrueForge"
TF_URL="${TRUEFORGE_BASE_URL:-http://localhost:8790}"
TF_PORT=$(echo "$TF_URL" | sed -E 's#.*:([0-9]+).*#\1#')
start TrueForge "$TF_PORT" trueforge.log "$ROOT/agent" ./start_trueforge.sh --port "$TF_PORT"
wait_for TrueForge 180 curl -sf "$TF_URL/api/v1/capabilities"

say "4. Registering tools, model, sandbox, skill (SKILL_REF=${SKILL_REF:-main}) and agents"
(cd "$ROOT/agent" && uv run setup.py)

say "5. Node UI server"
[ -d "$ROOT/node_modules" ] || (cd "$ROOT" && npm install --silent)
start "Node server" "${PORT:-3000}" node.log "$ROOT" node server.js
wait_for "Node server" 30 curl -sf "http://localhost:${PORT:-3000}/"

say "QuoteForge is running: http://localhost:${PORT:-3000}"
echo "  TrueForge UI: $TF_URL    Logs: $LOGS/"
if [ ${#STARTED[@]} -gt 0 ]; then
  echo "  Press Ctrl-C to stop the services this script started."
  wait
fi
