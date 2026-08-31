"""The capability catalogue, derived from the frozen adapter SPI (REQ-030).

A manifest declares which capabilities of its kind the adapter actually
supports, because no adapter implements every method of its kind - the SPI says
so itself: "unsupported optional capabilities raise NotSupportedError, and the
registry manifest must declare the capabilities that are supported".

The catalogue is not a second list to keep in step with the SPI; it is read off
the SPI's abstract methods. Add a method to LogsAdapter and it becomes a
declarable capability on the same commit, and any manifest naming a capability
that is not an SPI method is rejected at load time rather than failing later
against an AttributeError.

`ping` is excluded: it is on the base class, every adapter must answer it, and
the connector health loop depends on that being unconditional.
"""

from __future__ import annotations

from typing import Mapping

from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.spi import ADAPTER_SPI_BY_KIND, Adapter

_ALWAYS_REQUIRED = frozenset({"ping"})


def _catalogue() -> dict[AdapterKind, frozenset[str]]:
    base = set(Adapter.__abstractmethods__)
    return {
        kind: frozenset(set(spi.__abstractmethods__) - base - _ALWAYS_REQUIRED)
        for kind, spi in ADAPTER_SPI_BY_KIND.items()
    }


CAPABILITIES_BY_KIND: Mapping[AdapterKind, frozenset[str]] = _catalogue()

MUTATING_CAPABILITIES: Mapping[AdapterKind, frozenset[str]] = {
    # Capabilities that change the integrated system rather than read it. This
    # is what the credential-scope floor is defined against: a read-only
    # credential may never reach one of these, whatever a policy or an operator
    # later says. Knowledge indexing is absent on purpose - it writes into
    # SignalForge's own index, not into the customer's estate.
    AdapterKind.ACTIONS: frozenset({"execute"}),
    AdapterKind.CHAOS: frozenset({"inject", "abort"}),
    AdapterKind.ITSM: frozenset({"create", "update"}),
    AdapterKind.COLLABORATION: frozenset({"post"}),
    AdapterKind.NOTIFICATIONS: frozenset({"send"}),
}

NORMALIZE_CAPABILITY = "normalize"
"""Push-mode adapters translate an inbound payload; that is what makes them
push-mode rather than a poller."""

REDACTION_REQUIRED_KINDS: frozenset[AdapterKind] = frozenset(
    {
        AdapterKind.LOGS,
        AdapterKind.TRACES,
        AdapterKind.COLLABORATION,
        AdapterKind.KNOWLEDGE,
        AdapterKind.ITSM,
    }
)
"""Kinds that carry free text out of the customer's systems and into evidence,
prompts and briefs. Those manifests must say what gets redacted before the
adapter is allowed to exist; the empty answer has to be written down."""


def capabilities_for(kind: AdapterKind) -> frozenset[str]:
    return CAPABILITIES_BY_KIND[kind]


def mutating_for(kind: AdapterKind) -> frozenset[str]:
    return MUTATING_CAPABILITIES.get(kind, frozenset())


def is_mutating(kind: AdapterKind, capability: str) -> bool:
    return capability in mutating_for(kind)


def unknown_capabilities(kind: AdapterKind, declared: frozenset[str]) -> tuple[str, ...]:
    return tuple(sorted(declared - capabilities_for(kind)))
