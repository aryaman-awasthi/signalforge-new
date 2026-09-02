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

import time
from datetime import datetime
from typing import Callable

from signalforge.contracts.canonical import ProbeResult, ProbeSpec
from signalforge.contracts.errors import AdapterTimeoutError, AdapterUnavailableError
from signalforge.infrastructure.adapters.transport import HttpTransport

DETAIL_MAX_CHARS = 200


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
        status, _ = await transport.send(
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
    return ProbeResult(
        probe_id=probe.probe_id,
        target=probe.target,
        ok=ok,
        latency_ms=latency_ms,
        status_code=status,
        detail=None if ok else f"expected {probe.expected_status}, got {status}",
        executed_at=now(),
    )
