"""The five ingest streams, derived from the frozen contract (REQ-032, REQ-004).

`INGEST_PATHS` in `contracts/ingest.py` names the five paths SignalForge
exposes. This module is the domain's view of the same five: which canonical
model each one carries, which adapter kind can translate a vendor payload into
it, what topic an accepted item is published under, and how one item is
validated. The enum is checked against the contract at import time, so a stream
cannot be added here without adding the path there - or removed there without
this module refusing to load.

Topics are derived from the payload rather than fixed per stream, because a
started deployment and a completed one are different facts and correlation reads
them differently. `deployment.started` and `deployment.completed` are the two
well-known topics the envelope contract already names.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Mapping, Optional

from pydantic import BaseModel, ValidationError

from signalforge.contracts.canonical import (
    Alert,
    AlertStatus,
    ChangeEvent,
    ChangeType,
    CollabMessage,
    Ticket,
    TopologySnapshot,
)
from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.ingest import INGEST_PATHS, MAX_BATCH_ITEMS, IngestItem
from signalforge.domain.ingest.errors import IngestPayloadError

MAX_SOURCE_SYSTEM_CHARS = 100


class IngestStream(str, Enum):
    CHANGES = "changes"
    ALERTS = "alerts"
    TOPOLOGY = "topology"
    MESSAGES = "messages"
    TICKETS = "tickets"

    @property
    def path(self) -> str:
        return INGEST_PATHS[self.value]

    @property
    def payload_model(self) -> type[BaseModel]:
        return PAYLOAD_MODELS[self]

    @property
    def adapter_kind(self) -> AdapterKind:
        return ADAPTER_KINDS[self]


if {stream.value for stream in IngestStream} != set(INGEST_PATHS):
    # A drift guard rather than a test, because a stream the router serves and
    # the domain does not know about would be an unvalidated endpoint.
    raise RuntimeError(
        "IngestStream and contracts.ingest.INGEST_PATHS disagree: "
        f"{sorted(s.value for s in IngestStream)} vs {sorted(INGEST_PATHS)}"
    )


PAYLOAD_MODELS: dict[IngestStream, type[BaseModel]] = {
    IngestStream.CHANGES: ChangeEvent,
    IngestStream.ALERTS: Alert,
    IngestStream.TOPOLOGY: TopologySnapshot,
    IngestStream.MESSAGES: CollabMessage,
    IngestStream.TICKETS: Ticket,
}

ADAPTER_KINDS: dict[IngestStream, AdapterKind] = {
    IngestStream.CHANGES: AdapterKind.CHANGES,
    IngestStream.ALERTS: AdapterKind.ALERTS,
    IngestStream.TOPOLOGY: AdapterKind.TOPOLOGY,
    IngestStream.MESSAGES: AdapterKind.COLLABORATION,
    IngestStream.TICKETS: AdapterKind.ITSM,
}

TOPOLOGY_TOPIC = "topology.updated"
MESSAGE_TOPIC = "collaboration.message.received"
TICKET_TOPIC = "itsm.ticket.updated"


def topic_for(stream: IngestStream, payload: BaseModel) -> str:
    """The topic an accepted item is published under."""
    if stream is IngestStream.ALERTS:
        assert isinstance(payload, Alert)
        return (
            "alert.fired" if payload.status is AlertStatus.FIRING else "alert.resolved"
        )
    if stream is IngestStream.CHANGES:
        assert isinstance(payload, ChangeEvent)
        namespace = (
            "deployment" if payload.change_type is ChangeType.DEPLOYMENT else "change"
        )
        return f"{namespace}.{payload.status.value}"
    return {
        IngestStream.TOPOLOGY: TOPOLOGY_TOPIC,
        IngestStream.MESSAGES: MESSAGE_TOPIC,
        IngestStream.TICKETS: TICKET_TOPIC,
    }[stream]


def describe_validation_error(exc: ValidationError, limit: int = 3) -> str:
    """A compact, sender-readable reason.

    The whole pydantic error list would be most of the response body for a
    batch, so the first few are named with their field path - enough for an
    integrator to find the field without handing back an internal stack shape.
    """
    parts = []
    for error in exc.errors()[:limit]:
        location = ".".join(str(item) for item in error.get("loc", ()) if item != "payload")
        parts.append(f"{location or 'payload'}: {error.get('msg', 'invalid')}")
    remainder = len(exc.errors()) - limit
    if remainder > 0:
        parts.append(f"(+{remainder} more)")
    return "; ".join(parts)


def validate_envelope(raw: Any) -> tuple[str, bool, list[Any]]:
    """Check the batch wrapper before a single item is looked at.

    The wrapper is all-or-nothing (a 422) while items are individually
    rejectable (a 202 listing them), because a batch that names no source system
    or carries a thousand items is a broken sender, not a broken record.
    """
    if not isinstance(raw, Mapping):
        raise IngestPayloadError("an ingest request body must be a JSON object")
    unknown = set(raw) - {"source_system", "simulated", "items"}
    if unknown:
        raise IngestPayloadError(f"unknown ingest request fields {sorted(unknown)}")
    source_system = raw.get("source_system")
    if not isinstance(source_system, str) or not source_system.strip():
        raise IngestPayloadError("source_system is required and must be a non-empty string")
    if len(source_system) > MAX_SOURCE_SYSTEM_CHARS:
        raise IngestPayloadError(
            f"source_system must be at most {MAX_SOURCE_SYSTEM_CHARS} characters"
        )
    simulated = raw.get("simulated", False)
    if not isinstance(simulated, bool):
        raise IngestPayloadError("simulated must be a boolean")
    items = raw.get("items")
    if not isinstance(items, list) or not items:
        raise IngestPayloadError("items must be a non-empty list")
    if len(items) > MAX_BATCH_ITEMS:
        raise IngestPayloadError(
            f"a batch carries at most {MAX_BATCH_ITEMS} items, got {len(items)}"
        )
    return source_system.strip(), simulated, items


ITEM_MODELS: dict[IngestStream, type[BaseModel]] = {
    # Built once: parametrising a generic model rebuilds its validator, and a
    # 500-item batch would otherwise pay for that 500 times.
    stream: IngestItem[model]  # type: ignore[valid-type]
    for stream, model in PAYLOAD_MODELS.items()
}


def validate_item(stream: IngestStream, raw: Any) -> IngestItem:
    """Validate one item against the frozen contract shape for this stream.

    Raises IngestPayloadError with a reason the caller puts in the ack's
    `rejected` list; the batch's other items are unaffected.
    """
    try:
        return ITEM_MODELS[stream].model_validate(raw)
    except ValidationError as exc:
        raise IngestPayloadError(describe_validation_error(exc)) from exc


def validate_payload(stream: IngestStream, raw: Any) -> BaseModel:
    """Validate a bare payload - what an adapter's `normalize` produced, which
    has not been through the item wrapper."""
    try:
        return stream.payload_model.model_validate(raw)
    except ValidationError as exc:
        raise IngestPayloadError(describe_validation_error(exc)) from exc


def item_event_id(raw: Any) -> Optional[str]:
    """The producer's event id in canonical form, read before validation.

    Read separately, and normalised through `uuid.UUID`, for two reasons: a
    duplicate of an item that would *also* fail validation is still recognised
    as a duplicate rather than reported as a new schema error on every retry,
    and `{ABCD-...}` upper-case and hyphenated spellings of one id are one id.
    """
    if isinstance(raw, Mapping):
        value = raw.get("event_id")
        if isinstance(value, str) and value.strip():
            try:
                return str(uuid.UUID(value.strip()))
            except ValueError:
                return None
    return None
