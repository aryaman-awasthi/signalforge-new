#!/usr/bin/env bash
# Set a *running* SignalForge up to the moment before a declaration.
#
#   bash run.sh          # in one terminal
#   bash scripts/demo.sh # in another
#
# This is the live half of the demonstration: it drives the reference harness
# and the platform over HTTP - signing up, onboarding through the public
# contracts, inviting the people an incident needs, driving steady traffic until
# a baseline exists, injecting the published fault, and then stopping, because
# what happens next is a person pressing Declare.
#
# The deterministic half is `pytest signalforge/tests/drills` - the whole
# Recovery Collision incident, on a virtual clock, in under a minute, with no
# model and no network. Use that when a machine has to be convinced; use this
# when a person has to be shown.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python"

API="${SIGNALFORGE_API:-http://127.0.0.1:8000}"
APP="${SIGNALFORGE_APP_BASE_URL:-http://127.0.0.1:8080}"
DRILL="${1:-$ROOT/drills/s4-acceptance.yaml}"

if [ ! -f "$DRILL" ]; then
  echo "[demo] no such drill: $DRILL" >&2
  echo "[demo] drills are demo data (CLAUDE.md rule 7); the product runs without them" >&2
  exit 1
fi

echo "[demo] platform  $API"
echo "[demo] harness   $APP"
echo "[demo] scenario  $(basename "$DRILL")"
echo

exec "$PY" "$ROOT/scripts/drill_run.py" "$DRILL" --api "$API" --app-base-url "$APP"
