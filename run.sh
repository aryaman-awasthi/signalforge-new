#!/usr/bin/env bash
# SignalForge single-machine launcher (no Docker).
#
# Boots the reference harness, applies migrations, starts the API and the web
# app, then *waits until each one answers* and prints where it is. Two rules,
# and both exist because a demo that starts silently and half-works is worse
# than one that refuses:
#
#   1. Nothing is reported ready until it has answered a real request.
#   2. If something essential does not come up, this exits non-zero with the
#      log path, rather than leaving a half-started estate behind.
#
# The critical path needs no Docker, no network and no GENAILAB_API_KEY. With
# the key absent the platform runs deterministically and says so on every AI
# surface; that is a supported mode, not a degraded install.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Configuration is read from the process environment (signalforge/config.py reads
# os.environ and nothing else) and nothing was ever loading .env, so a machine
# with a perfectly good gateway key still ran every AI surface in degraded mode.
# The file the operator is told to write in docs/09 is now the file the platform
# sees. .env wins over an ambient value; to override one for a single boot,
# comment it out of .env rather than exporting it.
if [ -f "$ROOT/.env" ]; then set -a; . "$ROOT/.env"; set +a; fi

PY="$ROOT/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python"

API_HOST="${SIGNALFORGE_HOST:-127.0.0.1}"
API_PORT="${SIGNALFORGE_PORT:-8000}"
WEB_PORT="${SIGNALFORGE_WEB_PORT:-3000}"
# The reference harness reads its own port from its own environment; this
# file may not name the reference application (CLAUDE.md rule 2), so the
# knob here is generic and the harness's run.sh maps it.
HARNESS_PORT="${SIGNALFORGE_HARNESS_PORT:-8080}"
LOG_DIR="$ROOT/var/run"
READY_TIMEOUT_S="${SIGNALFORGE_READY_TIMEOUT_S:-120}"

mkdir -p "$LOG_DIR"
pids=()
started=0

say() { echo "[run] $*"; }
fail() { echo "[run] FAILED: $*" >&2; kill "${pids[@]}" 2>/dev/null || true; exit 1; }

wait_for() {
  # wait_for <name> <url> <logfile> - poll until it answers or the budget runs out.
  local name="$1" url="$2" log="$3"
  local deadline=$((SECONDS + READY_TIMEOUT_S))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if "$PY" - "$url" <<'PROBE' >/dev/null 2>&1
import sys, urllib.error, urllib.request

# Any HTTP status is "it answered". A guarded path returns 401 and that is a
# component that is up - readiness is about the socket and the application
# behind it, not about this probe holding a credential.
try:
    with urllib.request.urlopen(sys.argv[1], timeout=2) as response:
        sys.exit(0 if response.status < 500 else 1)
except urllib.error.HTTPError as answered:
    sys.exit(0 if answered.code < 500 else 1)
except Exception:
    sys.exit(1)
PROBE
    then
      say "$name is up"
      return 0
    fi
    sleep 1
  done
  fail "$name did not answer $url within ${READY_TIMEOUT_S}s - see $log"
}

# --- the reference harness (demo data; deleting it leaves a working product) ---
# Discovered by glob rather than by name, because this file is core and may not
# name the reference application (CLAUDE.md rule 2).
for harness in "$ROOT"/reference-app/*/run.sh; do
  [ -f "$harness" ] || continue
  harness_dir="$(dirname "$harness")"
  harness_name="$(basename "$harness_dir")"
  if ls "$harness_dir"/target/*.jar >/dev/null 2>&1 || command -v mvn >/dev/null 2>&1; then
    say "starting reference harness $harness_name on :$HARNESS_PORT"
    (cd "$harness_dir" && bash run.sh) >"$LOG_DIR/harness.log" 2>&1 &
    pids+=($!)
    started=1
    wait_for "reference harness" "http://127.0.0.1:$HARNESS_PORT/observe/health" \
      "$LOG_DIR/harness.log"
  else
    say "reference harness $harness_name: not built and maven is absent - skipping"
    say "  (the platform does not need it; the demo drill does)"
  fi
done

# --- the platform -------------------------------------------------------------
if [ ! -f "$ROOT/signalforge/api/main.py" ]; then
  fail "signalforge/api/main.py is missing - this is not a SignalForge checkout"
fi

say "applying database migrations"
(cd "$ROOT" && "$PY" -m alembic upgrade head) >"$LOG_DIR/migrate.log" 2>&1 \
  || fail "migrations did not apply - see $LOG_DIR/migrate.log"

say "starting api on :$API_PORT"
(cd "$ROOT" && "$PY" -m uvicorn signalforge.api.main:app \
  --host "$API_HOST" --port "$API_PORT") >"$LOG_DIR/api.log" 2>&1 &
pids+=($!)
started=1
wait_for "api" "http://$API_HOST:$API_PORT/health" "$LOG_DIR/api.log"

# --- the cockpit ---------------------------------------------------------------
if [ -f "$ROOT/web/package.json" ]; then
  if [ -d "$ROOT/web/node_modules" ]; then
    say "starting web on :$WEB_PORT"
    (cd "$ROOT/web" && npm run dev -- --port "$WEB_PORT") >"$LOG_DIR/web.log" 2>&1 &
    pids+=($!)
    started=1
    wait_for "web" "http://127.0.0.1:$WEB_PORT/" "$LOG_DIR/web.log"
  else
    say "web: node_modules is absent - run 'npm install' in web/ first"
    say "  (the API and the drill are unaffected)"
  fi
fi

if [ "$started" -eq 0 ]; then
  fail "nothing started"
fi

cat <<BANNER

  SignalForge is up.

    cockpit        http://127.0.0.1:$WEB_PORT
    api            http://$API_HOST:$API_PORT
    api docs       http://$API_HOST:$API_PORT/docs
    health         http://$API_HOST:$API_PORT/health
    metrics        http://$API_HOST:$API_PORT/metrics
    reference app  http://127.0.0.1:$HARNESS_PORT

  Logs: $LOG_DIR
  Model: $( [ -n "${GENAILAB_API_KEY:-}" ] && echo "gateway configured" || echo "no key set - deterministic mode, which is supported" )

  Next: bash scripts/demo.sh      (sets the cockpit up to a live P0 proposal)
        python scripts/demo_check.py   (verifies the twelve final-demo checks)

  Ctrl-C stops everything.

BANNER

trap 'kill "${pids[@]}" 2>/dev/null' INT TERM
wait
