"""What a model call is allowed to fail with (REQ-051).

The taxonomy exists so that callers can tell the three failures apart that lead
to *different* behaviour, and so that infrastructure never leaks a vendor
exception - an httpx error, a JSON decode error - into the application layer:

- `LlmUnavailable` is transient or environmental. The caller degrades.
- `LlmBudgetExceeded` is this platform's own decision, not the gateway's. It is
  separate because "we chose to stop spending on this incident" and "the model
  is down" must never be reported to a commander as the same thing.
- `LlmMisconfigured` is a deployment error - no key, or an endpoint that is not
  the approved gateway. It never reaches a caller at call time: the provider
  refuses to build a live client and runs deterministically instead, so a
  misconfiguration degrades the product rather than stopping it.
"""

from __future__ import annotations


class LlmError(Exception):
    """Base for every model-path failure."""


class LlmUnavailable(LlmError):
    """The model could not be reached, timed out, or answered unusably."""


class LlmBudgetExceeded(LlmError):
    """This incident has spent its token budget."""


class LlmMisconfigured(LlmError):
    """Configuration does not describe a usable, approved gateway."""
