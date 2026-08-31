"""Adapter-registry domain errors.

They subclass the four shared categories so api/errors.py maps them without a
new handler: a missing adapter or binding is a 404, a scope violation is a 422
because it is a rejected instruction rather than a permission decision about the
caller - the caller may well hold manage_apps and still be refused, because the
floor is about the credential, not the person.
"""

from __future__ import annotations

from signalforge.domain.shared.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
)


class AdapterNotFoundError(NotFoundError):
    """No manifest with that id is installed in this deployment."""


class ConnectorBindingNotFoundError(NotFoundError):
    """No such binding in this tenant's scope."""


class DuplicateBindingError(ConflictError):
    """This app already binds that adapter at that credential scope. Scopes are
    separate bindings on purpose (read-only and remediation never share a
    credential), so the uniqueness key includes the scope."""


class ConnectorConfigError(DomainValidationError):
    """The binding's config does not satisfy the manifest's config_schema."""


class CredentialScopeError(DomainValidationError):
    """A credential was pointed at something its scope does not authorise.

    Permanent floor (CLAUDE.md rule 4): a read-only credential can never drive a
    mutating capability, and chaos runs only on a drill-scoped credential.
    """
