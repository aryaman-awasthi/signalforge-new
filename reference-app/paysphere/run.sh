#!/usr/bin/env bash
# Build the reference harness if it is stale, then run it.
#
# Nothing here is required by SignalForge: the platform reaches this application
# over HTTP and would not notice if it were rewritten in another language, which
# is the entire point of the /observe contract being frozen and public.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JAR="$HERE/target/paysphere.jar"

# PAYSPHERE_PORT is this application's own knob; SIGNALFORGE_HARNESS_PORT is the
# generic one the platform's launcher sets, since that launcher may not name
# this application.
export PAYSPHERE_PORT="${PAYSPHERE_PORT:-${SIGNALFORGE_HARNESS_PORT:-8080}}"
export PAYSPHERE_DATA_DIR="${PAYSPHERE_DATA_DIR:-$HERE/data}"
export PAYSPHERE_LOG_DIR="${PAYSPHERE_LOG_DIR:-$HERE/logs}"

needs_build=0
if [ ! -f "$JAR" ]; then
  needs_build=1
elif [ -n "$(find "$HERE/src" "$HERE/pom.xml" -newer "$JAR" -type f 2>/dev/null | head -1)" ]; then
  needs_build=1
fi

if [ "$needs_build" = "1" ]; then
  if ! command -v mvn >/dev/null 2>&1; then
    echo "[harness] maven is not on PATH and $JAR is missing or stale" >&2
    echo "[harness] install maven, or build elsewhere and drop the jar at $JAR" >&2
    exit 1
  fi
  echo "[harness] building"
  (cd "$HERE" && mvn -B -q -DskipTests package)
fi

echo "[harness] starting on :$PAYSPHERE_PORT (data: $PAYSPHERE_DATA_DIR)"
exec java -jar "$JAR"
