"""Incident-brain domain errors.

Subclasses of the four shared categories, so the API's single error mapper
(`api/errors.py`) already knows what to do with them.
"""

from __future__ import annotations

from signalforge.domain.shared.errors import DomainValidationError, NotFoundError


class BrainValidationError(DomainValidationError):
    """A graph node, edge or score that cannot be constructed as asked."""


class BrainNotFoundError(NotFoundError):
    """An incident with no brain, or a node id that names nothing."""


class UngroundedAnswerError(DomainValidationError):
    """A model answer that cited something the retrieval never returned.

    Raised inside the copilot rather than surfaced: an ungrounded answer is
    replaced by an "insufficient evidence" one, never shown with a warning.
    """
