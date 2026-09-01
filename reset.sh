#!/usr/bin/env bash
# Reset local runtime state to a clean slate. Never touches source, specs,
# drills or seeds definitions - only generated runtime data and caches.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[reset] clearing runtime data (var/)"
rm -rf "$ROOT/var"
mkdir -p "$ROOT/var"

echo "[reset] clearing reference harness databases and logs"
rm -rf "$ROOT"/reference-app/*/data "$ROOT"/reference-app/*/logs

echo "[reset] clearing caches"
rm -rf "$ROOT/.pytest_cache"
find "$ROOT/signalforge" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# Later sprints append here: reseed databases, re-provision demo drills.
echo "[reset] done"
