#!/usr/bin/env bash
# App-agnostic core guard (CLAUDE.md ground rule 2).
# The reference-app name may appear only in: reference-app/, drills/, seeds/,
# test directories, and the reference harness config directory.
# The pattern is assembled from halves so this script never trips its own scan.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATTERN="pay""sphere"

matches="$(grep -rIni "$PATTERN" "$ROOT" \
  --exclude-dir=.git \
  --exclude-dir=.venv \
  --exclude-dir=node_modules \
  --exclude-dir=.next \
  --exclude-dir=__pycache__ \
  --exclude-dir=.pytest_cache \
  --exclude-dir=.claude \
  --exclude-dir=playbook \
  --exclude-dir=var \
  --exclude-dir=bin \
  --exclude-dir=reference-app \
  --exclude-dir=drills \
  --exclude-dir=seeds \
  --exclude-dir=tests \
  --exclude-dir="$PATTERN-config" \
  --exclude=CLAUDE.md \
  2>/dev/null)"

if [ -n "$matches" ]; then
  echo "check_clean: FAILED - app-specific string found outside allowed paths:"
  echo "$matches"
  exit 1
fi

echo "check_clean: OK - core is app-agnostic"
exit 0
