#!/usr/bin/env bash
# SignalForge single-machine launcher (no Docker). Boots every component that
# exists so far; components land in later sprints (api: S1, web: S0-T05).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python"

started=0
pids=()

# Reference harnesses are demo data (CLAUDE.md rule 7). Each one owns a run.sh and
# is started only once it has been built; deleting reference-app/ leaves this
# script and the product working. Discovered by glob rather than by name, because
# this file is core and may not name the reference application (rule 2).
for harness in "$ROOT"/reference-app/*/run.sh; do
  [ -f "$harness" ] || continue
  harness_dir="$(dirname "$harness")"
  if ls "$harness_dir"/target/*.jar >/dev/null 2>&1; then
    echo "[run] starting reference harness $(basename "$harness_dir")"
    (cd "$harness_dir" && bash run.sh) &
    pids+=($!)
    started=1
  else
    echo "[run] reference harness $(basename "$harness_dir"): not built (bash $harness builds and starts it)"
  fi
done

if [ -f "$ROOT/signalforge/api/main.py" ]; then
  if [ -f "$ROOT/alembic.ini" ]; then
    echo "[run] applying database migrations"
    (cd "$ROOT" && "$PY" -m alembic upgrade head)
  fi
  echo "[run] starting api on :8000"
  (cd "$ROOT" && "$PY" -m uvicorn signalforge.api.main:app --host 127.0.0.1 --port 8000) &
  pids+=($!)
  started=1
else
  echo "[run] api: not built yet"
fi

if [ -f "$ROOT/web/package.json" ]; then
  echo "[run] starting web on :3000"
  (cd "$ROOT/web" && npm run dev) &
  pids+=($!)
  started=1
else
  echo "[run] web: not built yet"
fi

if [ "$started" -eq 0 ]; then
  echo "[run] skeleton boot OK - no components to start yet"
  exit 0
fi

trap 'kill "${pids[@]}" 2>/dev/null' INT TERM
wait
