#!/usr/bin/env bash
# Apply this configuration repository to the application, and tell SignalForge.
#
# This is the application team's deployment pipeline, not part of SignalForge. It
# knows exactly two things about the platform: the URL of a frozen public ingest
# path, and a credential it was given. There is no SDK, no library and no shared
# code - the announcement is an HTTP POST of a canonical ChangeEvent, signed the
# way /api/v1/ingest/* requires.
#
#   POST <ingest url>
#     Authorization: Bearer <token>
#     X-SignalForge-Timestamp: <unix seconds>
#     X-SignalForge-Signature: hex hmac-sha256 over "<timestamp>.<raw body>"
#
# Two deliberate behaviours:
#
#   * The event_id is derived from (release, region, status), so re-running the
#     pipeline is a duplicate the platform acknowledges and does not re-apply.
#     A deployment announced twice must not look like two deployments - that is
#     precisely the correlation a change-related incident depends on.
#   * A failure to announce never fails the deploy. Monitoring is not allowed to
#     gate shipping. The script says loudly that the deployment went unannounced
#     and exits on the deploy's own result.
#
# Usage:
#   ./deploy.sh --region region-b [--release v13] [--dry-run]
#
# Environment:
#   PAYSPHERE_URL              default http://localhost:8080
#   PAYSPHERE_DEPLOY_KEY       default ps-deploy-8e5b0d3f9c67 (the app's own pipeline key)
#   SIGNALFORGE_INGEST_URL     default http://localhost:8000/api/v1/ingest/changes
#   SIGNALFORGE_INGEST_TOKEN   issued by SignalForge for the "changes" stream
#   SIGNALFORGE_INGEST_SECRET  the HMAC secret issued alongside it
#   SIGNALFORGE_INGEST_KEY_ID  optional; identifies the secret after a rotation
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PAYSPHERE_URL="${PAYSPHERE_URL:-http://localhost:8080}"
PAYSPHERE_DEPLOY_KEY="${PAYSPHERE_DEPLOY_KEY:-ps-deploy-8e5b0d3f9c67}"
INGEST_URL="${SIGNALFORGE_INGEST_URL:-http://localhost:8000/api/v1/ingest/changes}"
INGEST_TOKEN="${SIGNALFORGE_INGEST_TOKEN:-}"
INGEST_SECRET="${SIGNALFORGE_INGEST_SECRET:-}"
INGEST_KEY_ID="${SIGNALFORGE_INGEST_KEY_ID:-}"

REGION=""
RELEASE=""
DRY_RUN=0
ANNOUNCED_STARTED=0

die() { echo "deploy: $*" >&2; exit 2; }
warn() { echo "deploy: WARNING: $*" >&2; }
say() { echo "deploy: $*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --region) REGION="${2:-}"; shift 2 ;;
    --release) RELEASE="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) die "unknown argument $1" ;;
  esac
done

[ -n "$REGION" ] || die "--region is required (region-a or region-b)"
REGION_FILE="$HERE/$REGION.yml"
[ -f "$REGION_FILE" ] || die "no configuration for $REGION (expected $REGION_FILE)"
SHARED_FILE="$HERE/shared.yml"
[ -f "$SHARED_FILE" ] || die "missing $SHARED_FILE"

# --- read the configuration ---------------------------------------------------
# Deliberately parsed with sed rather than a YAML tool: the pipeline must run on a
# machine with nothing installed, and this repository's schema is two scalars.
scalar() { sed -n "s/^[[:space:]]*$2:[[:space:]]*\"\{0,1\}\([^\"#]*\)\"\{0,1\}[[:space:]]*\$/\1/p" "$1" | head -1 | sed 's/[[:space:]]*$//'; }

[ -n "$RELEASE" ] || RELEASE="$(scalar "$SHARED_FILE" release)"
[ -n "$RELEASE" ] || die "shared.yml declares no release and --release was not given"
SERVICE="$(scalar "$SHARED_FILE" service)"
[ -n "$SERVICE" ] || SERVICE="payments"
TIMEOUT_MS="$(scalar "$REGION_FILE" timeout_ms)"
POOL_SIZE="$(scalar "$REGION_FILE" pool_size)"
[ -n "$TIMEOUT_MS" ] || die "$REGION_FILE declares no timeout_ms"
[ -n "$POOL_SIZE" ] || die "$REGION_FILE declares no pool_size"

CHANGE_ID="$RELEASE-$REGION"
ACTOR="${USER:-${USERNAME:-pipeline}}"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

say "release $RELEASE -> $REGION (timeout_ms=$TIMEOUT_MS pool_size=$POOL_SIZE)"

# --- ingest plumbing ----------------------------------------------------------

# A UUID derived from the content, so the same announcement always carries the
# same event_id. Version and variant nibbles are set so it is a well-formed UUIDv5.
derive_event_id() {
  local seed="$1" hex g1 g2 g3 g4 g5 nibble
  hex="$(printf '%s' "$seed" | openssl dgst -sha256 | awk '{print $NF}')"
  g1="${hex:0:8}"; g2="${hex:8:4}"; g3="${hex:12:4}"; g4="${hex:16:4}"; g5="${hex:20:12}"
  g3="5${g3:1:3}"
  nibble=$(printf '%x' $(( (16#${g4:0:1} & 3) + 8 )))
  g4="${nibble}${g4:1:3}"
  printf '%s-%s-%s-%s-%s' "$g1" "$g2" "$g3" "$g4" "$g5"
}

# Announce one canonical ChangeEvent. Never fatal: returns 1 and warns.
announce() {
  local status="$1" completed_at="${2:-}" body_file event_id ts sig code response

  if [ -z "$INGEST_TOKEN" ] || [ -z "$INGEST_SECRET" ]; then
    warn "no ingest credential in the environment; deployment.$status not announced"
    return 1
  fi

  event_id="$(derive_event_id "$CHANGE_ID:$status")"
  body_file="$(mktemp)"
  {
    printf '{"source_system":"config-pipeline","simulated":false,"items":[{'
    printf '"event_id":"%s","occurred_at":"%s","payload":{' "$event_id" "$STARTED_AT"
    printf '"change_id":"%s","change_type":"deployment","service":"%s","status":"%s"' \
      "$CHANGE_ID" "$SERVICE" "$status"
    printf ',"started_at":"%s","version":"%s","actor":"%s"' "$STARTED_AT" "$RELEASE" "$ACTOR"
    if [ -n "$completed_at" ]; then printf ',"completed_at":"%s"' "$completed_at"; fi
    printf ',"diff_ref":"config-repo:%s/%s.yml@%s"' "$(basename "$HERE")" "$REGION" "$RELEASE"
    printf '}}]}'
  } > "$body_file"

  ts="$(date -u +%s)"
  # Signed over the exact bytes that go on the wire, streamed rather than
  # re-serialised, so the signature cannot pass here and fail on the platform.
  sig="$({ printf '%s.' "$ts"; cat "$body_file"; } | openssl dgst -sha256 -hmac "$INGEST_SECRET" | awk '{print $NF}')"

  response="$(mktemp)"
  set -- -sS -o "$response" -w '%{http_code}' -X POST "$INGEST_URL" \
    -H "Authorization: Bearer $INGEST_TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-SignalForge-Timestamp: $ts" \
    -H "X-SignalForge-Signature: $sig"
  if [ -n "$INGEST_KEY_ID" ]; then
    set -- "$@" -H "X-SignalForge-Key-Id: $INGEST_KEY_ID"
  fi
  code="$(curl "$@" --data-binary @"$body_file" 2>/dev/null)" || code="000"

  case "$code" in
    202)
      say "deployment.$status announced: $(cat "$response")"
      rm -f "$body_file" "$response"
      return 0
      ;;
    000)
      warn "SignalForge unreachable at $INGEST_URL; deployment.$status not announced"
      ;;
    401|403)
      warn "SignalForge refused the ingest credential ($code); deployment.$status not announced"
      ;;
    *)
      warn "SignalForge answered $code for deployment.$status: $(cat "$response")"
      ;;
  esac
  rm -f "$body_file" "$response"
  return 1
}

# --- apply --------------------------------------------------------------------

apply_to_app() {
  local body response code
  body="$(printf '{"release":"%s","settings":{"timeout_ms":%s,"pool_size":%s}}' \
    "$RELEASE" "$TIMEOUT_MS" "$POOL_SIZE")"
  response="$(mktemp)"
  code="$(curl -sS -o "$response" -w '%{http_code}' -X POST "$PAYSPHERE_URL/api/deploy/$REGION" \
    -H "Authorization: Bearer $PAYSPHERE_DEPLOY_KEY" \
    -H "Content-Type: application/json" \
    --data-binary "$body" 2>/dev/null)" || code="000"
  if [ "$code" = "200" ]; then
    say "applied: $(cat "$response")"
    rm -f "$response"
    return 0
  fi
  warn "the application refused the configuration ($code): $(cat "$response")"
  rm -f "$response"
  return 1
}

if [ "$DRY_RUN" = "1" ]; then
  say "dry run: would announce deployment.started, apply to $PAYSPHERE_URL, announce completed"
  say "dry run: event_id(started)   = $(derive_event_id "$CHANGE_ID:started")"
  say "dry run: event_id(completed) = $(derive_event_id "$CHANGE_ID:completed")"
  exit 0
fi

announce started && ANNOUNCED_STARTED=1

if apply_to_app; then
  announce completed "$(date -u +%Y-%m-%dT%H:%M:%SZ)" || true
  if [ "$ANNOUNCED_STARTED" = "0" ]; then
    say "deployed $RELEASE to $REGION; the deployment was NOT announced to SignalForge"
  else
    say "deployed $RELEASE to $REGION"
  fi
  exit 0
fi

# The change failed. Announcing it matters more than usual: an incident opened in
# the next five minutes is very likely about this.
announce failed "$(date -u +%Y-%m-%dT%H:%M:%SZ)" || true
die "release $RELEASE was not applied to $REGION"
