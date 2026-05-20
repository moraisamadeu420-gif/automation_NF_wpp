#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"

if [[ ! -d "$VENV" ]]; then
  echo "Virtual environment not found. Run 'make install' first."
  exit 1
fi

source "$VENV/bin/activate"

# Ensure tables exist
python -c "import asyncio; from app.database.connection import create_tables; asyncio.run(create_tables())"

exec uvicorn main:app \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-8000}" \
  --workers 1 \
  --log-level "${LOG_LEVEL:-info}"
