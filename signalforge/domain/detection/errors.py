"""Detection domain errors.

They subclass the shared categories so `api/errors.py` maps them without a new
handler. There is deliberately no "detection failed" error: a poll that could
not reach an application is a recorded *observation* (a stale signal), not an
exception, because the alternative is a detection loop that stops on the first
unreachable target and reports nothing about the other twenty applications.
"""

from __future__ import annotations

from signalforge.domain.shared.errors import DomainValidationError


class DetectionValidationError(DomainValidationError):
    """A caller asked detection for something it cannot compute or store.

    A negative rollup window, a signal key that is not mapped, a bucket that is
    not aligned to the rollup interval.
    """
