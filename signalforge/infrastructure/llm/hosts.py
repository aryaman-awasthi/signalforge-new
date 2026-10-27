"""Where model traffic is allowed to go.

This is a constant, not configuration, and that is the whole point. SignalForge
sends incident transcripts, runbooks, telemetry summaries and customer-facing
draft communications to whatever endpoint the model path resolves to, so "which
host" is a security control rather than a deployment preference: an environment
variable able to redirect it would make a leaked `.env` - or a typo - a data
exfiltration path.

The approved endpoints both speak the OpenAI-compatible protocol: the
organisation's GenAI Lab gateway, and the Claude API's compatibility layer at
api.anthropic.com, approved on an explicit instruction (2026-10-27, for a client
demonstration while the gateway was refusing the organisation's key). Keeping an
OpenAI-shaped client is therefore correct and is not evidence of public-API use;
where the bytes go is decided here, and only here.

An unapproved endpoint is not an exception at boot. `load_llm_settings` reports
it as not-configured, the provider builds no client, and the platform runs its
deterministic path - because refusing to start an incident command centre over a
model misconfiguration would be the wrong failure.
"""

from __future__ import annotations

from urllib.parse import urlsplit

APPROVED_LLM_HOSTS = frozenset({"genailab.tcs.in", "api.anthropic.com"})
"""Hostnames model traffic may reach. Exact matches only: no suffix rule, so
`genailab.tcs.in.example.net` is a different host and is refused.

`api.anthropic.com` is the Claude API's OpenAI-compatible endpoint
(`/v1/chat/completions`, bearer-authenticated with a Claude API key). It ignores
`response_format` - the provider restates every schema in the prompt and
validates the answer regardless - and serves no `/embeddings`, so embeddings
degrade to the structural path when it is in use."""

PROVIDER_BY_HOST = {"genailab.tcs.in": "genailab", "api.anthropic.com": "anthropic"}
"""The provider label an approved host answers as. Part of every cache key, so
an answer from one endpoint is never served as an answer from the other."""

APPROVED_SCHEMES = frozenset({"https"})
"""Prompts and incident text never travel in clear text."""


def endpoint_host(url: str) -> str:
    """The hostname of a configured endpoint, lowercased, port removed."""
    return (urlsplit((url or "").strip()).hostname or "").lower()


def provider_name(url: str, default: str) -> str:
    """The label for the endpoint the URL resolves to; `default` for a host
    this table has no name for (an unapproved one never gets a client anyway)."""
    return PROVIDER_BY_HOST.get(endpoint_host(url), default)


def endpoint_is_approved(url: str) -> bool:
    parts = urlsplit((url or "").strip())
    return (
        parts.scheme.lower() in APPROVED_SCHEMES
        and endpoint_host(url) in APPROVED_LLM_HOSTS
    )


def describe_endpoint(url: str) -> str:
    """A log- and event-safe description: scheme, host and path only.

    Query strings are dropped because some gateways accept a key there, and this
    string ends up in `system.llm.degraded`.
    """
    parts = urlsplit((url or "").strip())
    if not parts.scheme:
        return "<unset>"
    return f"{parts.scheme}://{parts.netloc}{parts.path}"
