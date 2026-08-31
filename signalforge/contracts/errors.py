"""Adapter error taxonomy. Frozen contract: infrastructure code raises these,
never vendor exceptions, so failure handling stays app- and vendor-agnostic."""

from __future__ import annotations


class AdapterError(Exception):
    """Base for every adapter failure."""


class AdapterAuthError(AdapterError):
    """Credentials rejected or insufficient credential scope."""


class AdapterTimeoutError(AdapterError):
    """The call did not complete in time; the outcome may be UNCERTAIN."""


class AdapterUnavailableError(AdapterError):
    """The integrated system cannot be reached."""


class NotSupportedError(AdapterError):
    """The adapter does not implement this optional capability.
    The registry's capability matrix (Sprint 3) declares what is supported."""


class ManifestValidationError(AdapterError):
    """An action or chaos manifest failed JSON-schema validation."""
