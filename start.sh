#!/usr/bin/env bash
#
# start.sh — set up the Python environment (if needed) and start LangLearn.
#
#   ./start.sh                 # listen on :5056
#   ./start.sh --port 9000     # listen on :9000
#   ./start.sh --help
#
# Creates a per-host virtualenv at .venv_<hostname> and installs requirements.txt
# the first time (or whenever requirements.txt changes), then runs the Flask server.

set -euo pipefail

PORT="${PORT:-5056}"
HOST="${HOST:-0.0.0.0}"
DATA_DIR=""

usage() {
  cat <<'EOF'
Usage: ./start.sh [OPTIONS]

Start the LangLearn server. Creates a Python virtualenv at .venv_<hostname>
(installing dependencies from requirements.txt) if it isn't already present,
then runs the Flask server.

Options:
  -p, --port PORT        Port to listen on (default: 5056)
  -H, --host HOST        Host to bind on (default: 0.0.0.0)
  -d, --data-dir DIR     Runtime data dir for SQLite DB (default: ./data)
  -h, --help             Show this help and exit

Environment variables (options take precedence):
  PORT                   Port to listen on (default: 5056)
  HOST                   Host to bind on (default: 0.0.0.0)
  LANGLEARN_DATA_DIR     Runtime data directory (default: ./data)
  FLASK_DEBUG            Set to "1" to enable debug / auto-reload mode
  OPENAI_API_KEY         Required to enable LLM lookups
  OPENAI_BASE_URL        Default: https://api.openai.com/v1
  OPENAI_MODEL           Default: gpt-4o-mini

Examples:
  ./start.sh
  ./start.sh --port 9000
  OPENAI_API_KEY=sk-... ./start.sh
  FLASK_DEBUG=1 ./start.sh
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--port)   PORT="$2"; shift 2 ;;
    --port=*)    PORT="${1#*=}"; shift ;;
    -H|--host)   HOST="$2"; shift 2 ;;
    --host=*)    HOST="${1#*=}"; shift ;;
    -d|--data)   DATA_DIR="$2"; shift 2 ;;
    --data-dir=*) DATA_DIR="${1#*=}"; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) echo "start.sh: unknown option: $1" >&2; echo >&2; usage >&2; exit 1 ;;
  esac
done

cd "$(dirname "$0")"

VENV=".venv_$(hostname)"
STAMP="$VENV/.req_stamp"
NEW_STAMP=$(sha256sum backend/requirements.txt 2>/dev/null | cut -d' ' -f1 || true)
NEED_INSTALL=0

if [[ ! -x "$VENV/bin/python" ]]; then
  echo ">> Creating virtualenv at $VENV ..."
  python3 -m venv "$VENV"
  NEED_INSTALL=1
fi

if [[ ! -f "$STAMP" || "$(cat "$STAMP" 2>/dev/null || true)" != "$NEW_STAMP" ]]; then
  NEED_INSTALL=1
elif ! "$VENV/bin/python" -c "import flask" 2>/dev/null; then
  echo ">> Flask not found in venv, reinstalling dependencies ..."
  NEED_INSTALL=1
fi

if [[ $NEED_INSTALL -eq 1 ]]; then
  echo ">> Installing dependencies from backend/requirements.txt ..."
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r backend/requirements.txt
  echo "$NEW_STAMP" > "$STAMP"
fi

export PORT HOST
[[ -n "$DATA_DIR" ]] && export LANGLEARN_DATA_DIR="$DATA_DIR"

DATA_DISPLAY="${LANGLEARN_DATA_DIR:-./data}"
echo ">> Starting LangLearn on http://${HOST}:${PORT} (data: ${DATA_DISPLAY})"
exec "$VENV/bin/python" -m backend.app