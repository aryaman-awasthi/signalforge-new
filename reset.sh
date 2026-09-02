#!/usr/bin/env bash
# Return this machine to the deterministic demo baseline.
#
# What that baseline *is*, precisely: no organisation, no application, no
# incident, no telemetry, no cached model responses - and every line of source,
# spec, drill and seed exactly where it was. SignalForge holds no demo data of
# its own; drills/ and seeds/ are data that a run consumes, never state a run
# mutates. So "reset" means "delete what a run wrote", and nothing else.
#
# It refuses to run anywhere that does not look like this repository, because a
# script whose job is `rm -rf` should be paranoid about where it is standing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Same .env load as run.sh: reset touches paths that config.py resolves, so it
# has to resolve them the same way the platform will.
if [ -f "$ROOT/.env" ]; then set -a; . "$ROOT/.env"; set +a; fi

# Markers a *release* actually contains. CLAUDE.md used to be one of them, and
# it is deliberately not in the package - so the shipped reset.sh refused to run
# inside the shipped product, which is the one place it has to work. Found by
# unzipping the archive and running it (S8-T10).
for marker in "signalforge/api/main.py" "specs/requirements.yaml" "run.sh"; do
  [ -e "$ROOT/$marker" ] || {
    echo "[reset] refusing: $ROOT does not look like a SignalForge checkout ($marker missing)" >&2
    exit 1
  }
done

say() { echo "[reset] $*"; }

# 1. Everything the platform wrote. var/ holds the SQLite databases, the rollup
#    store, the LLM cache, the RTM recording, the non-functional measurements
#    and the run logs. All of it is derived; none of it is source.
say "clearing platform runtime state (var/)"
rm -rf "$ROOT/var"
mkdir -p "$ROOT/var"

# 2. Everything the reference harness wrote. Its jar is left alone: rebuilding
#    it needs maven and a minute, and it is a build artefact rather than state.
say "clearing reference harness databases and logs"
rm -rf "$ROOT"/reference-app/*/data "$ROOT"/reference-app/*/logs

# 3. Caches. Not state, but a stale one makes a rerun ambiguous.
say "clearing test and bytecode caches"
rm -rf "$ROOT/.pytest_cache"
find "$ROOT/signalforge" "$ROOT/scripts" -type d -name __pycache__ -prune \
  -exec rm -rf {} + 2>/dev/null || true

# 4. Migrations are applied by run.sh against the empty database, so there is
#    nothing to do here. Stating it rather than leaving it implicit: a reset
#    that half-migrated would be worse than one that did not migrate at all.
say "database will be migrated from empty on the next run.sh"

cat <<'DONE'

  Baseline restored.

    deleted   var/ (databases, rollups, LLM cache, RTM recording, logs)
              reference-app/*/data, reference-app/*/logs
              __pycache__, .pytest_cache
    untouched source, specs/, drills/, seeds/, observability/, the reference
              config repository, node_modules, the harness jar, and anything
              outside this checkout

  Next: bash run.sh

DONE
