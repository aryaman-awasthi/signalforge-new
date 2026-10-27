"""The synthetic probe SignalForge runs itself (REQ-031).

Shared by every health runtime rather than owned by one, because a probe is not
a translation of something the target publishes: it is SignalForge making a
request and timing it. Two health adapters measuring that differently would
report two latencies for the same journey and there would be no way to say which
one the SLO meant.

The one judgement it encodes is that **a non-2xx is a result, not an error**.
`expected_status` is what decides, and a probe that raised on 500 could never
assert that a page correctly returns 500. A timeout and an unreachable target
are results too - a failed one, carrying the reason - because a probe that
raised there would take a journey off the board at exactly the moment the
journey is broken.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Callable

from signalforge.contracts.canonical import ProbeResult, ProbeSpec
from signalforge.contracts.errors import AdapterTimeoutError, AdapterUnavailableError
from signalforge.infrastructure.adapters.transport import HttpTransport

DETAIL_MAX_CHARS = 200
EXCERPT_MAX_CHARS = 140
_EXCERPT_FIELDS = ("error_code", "error", "code", "reason", "detail", "message")


def body_excerpt(body: str) -> str:
    """What the target said, in a sentence.

    A failed probe that reports only "expected 200, got 403" tells the room that
    the journey is broken and nothing about why. The application's own answer -
    a `LICENSE_EXPIRED` code, a "pool exhausted" message - is the evidence a
    hypothesis is named from, so the first line of it rides along. JSON bodies
    are read for their error fields, in the order a client would look; anything
    else is the start of the text with its whitespace folded. Bounded, because a
    probe target may answer with a page and a page is not a reason.
    """
    text = (body or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        parts = [
            str(parsed[field]).strip()
            for field in _EXCERPT_FIELDS
            if isinstance(parsed.get(field), (str, int, float)) and str(parsed[field]).strip()
        ]
        if parts:
            text = " - ".join(dict.fromkeys(parts))
    folded = re.sub(r"\s+", " ", text)
    return folded[:EXCERPT_MAX_CHARS]


async def run_synthetic_probe(
    transport: HttpTransport, probe: ProbeSpec, now: Callable[[], datetime]
) -> ProbeResult:
    """Send one request and report what happened.

    `probe.target` may be an absolute URL or a path relative to the binding's
    base URL, so a journey probe can exercise a public entry point rather than
    only the endpoint the adapter reads. `now` is the platform clock, called
    once the attempt is over, so a drill's virtual clock dates the result.
    """
    started = time.perf_counter()
    try:
        status, body = await transport.send(
            probe.method.upper(), probe.target, timeout_s=probe.timeout_s
        )
    except (AdapterTimeoutError, AdapterUnavailableError) as exc:
        return ProbeResult(
            probe_id=probe.probe_id,
            target=probe.target,
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            detail=str(exc)[:DETAIL_MAX_CHARS],
            executed_at=now(),
        )
    latency_ms = (time.perf_counter() - started) * 1000.0
    ok = status == probe.expected_status
    detail = None
    if not ok:
        excerpt = body_excerpt(body)
        detail = (
            f"expected {probe.expected_status}, got {status}"
            + (f": {excerpt}" if excerpt else "")
        )[:DETAIL_MAX_CHARS]
    return ProbeResult(
        probe_id=probe.probe_id,
        target=probe.target,
        ok=ok,
        latency_ms=latency_ms,
        status_code=status,
        detail=detail,
        executed_at=now(),
    )
