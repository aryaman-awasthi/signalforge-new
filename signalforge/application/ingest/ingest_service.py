"""Accepting a pushed batch (REQ-032, REQ-017, REQ-013, REQ-020).

The order of the checks is the design, because each one is only safe once the
one before it has passed:

1. **A bearer token, and only a bearer token.** It resolves - by sha256, so what
   is stored is not usable - to exactly one key, and that key carries the tenant
   and the app everything in the request will be written under. An
   unauthenticated HTTP request has no other way to say who it is.
2. **A rate limit, per key, before any expensive work.** Applied here rather
   than after verification so that a flood cannot make the platform hash
   megabytes, and deliberately *not* audited: an audit row per refused request
   would let the flood write the disk it was refused from touching.
3. **The HMAC, over the exact bytes received.** The body is read once, kept as
   bytes, and verified before it is parsed - so nothing malformed, oversized or
   hostile is handed to a JSON parser on an unverified request. The timestamp
   must be inside the replay window, which is what stops a captured request
   from being replayed tomorrow.
4. **The stream.** A key posts to one stream, so a token leaked from a deploy
   script cannot inject alerts.

Only then is the body parsed. From there the batch wrapper is all-or-nothing
(422: a sender that names no source system is broken) while individual items are
individually rejectable (202 listing them: one malformed alert in five hundred
must not lose the other four hundred and ninety-nine).

Everything a batch does is one transaction: the receipts, the events they
became, the counters on the key and any audit entry. A receipt written without
its event would silently swallow the sender's retry, which is the one failure
mode this whole file exists to prevent.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from pydantic import BaseModel

from signalforge.application.adapters.invoker import ConnectorInvoker
from signalforge.application.events.bus import EventBus
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.contracts.envelope import (
    Actor,
    ActorType,
    Authority,
    EventEnvelope,
    Plane,
    Redaction,
    Source,
)
from signalforge.contracts.errors import AdapterError
from signalforge.contracts.ingest import (
    HDR_SIGNATURE,
    HDR_TIMESTAMP,
    IngestAck,
    IngestItem,
    IngestRejection,
)
from signalforge.domain.identity.ports import Clock, RateLimiter
from signalforge.domain.ingest import redaction as ingest_redaction
from signalforge.domain.ingest.errors import (
    IngestAuthError,
    IngestPayloadError,
    IngestStreamError,
)
from signalforge.domain.ingest.keys import (
    IngestKey,
    bearer_token,
    hash_ingest_token,
    secret_name,
)
from signalforge.domain.ingest.receipts import IngestReceipt
from signalforge.domain.ingest.streams import (
    IngestStream,
    item_event_id,
    topic_for,
    validate_envelope,
    validate_item,
    validate_payload,
)
from signalforge.domain.integrations.topology import assert_consistent
from signalforge.infrastructure.security.hmac_verify import IngestHmacVerifier

logger = logging.getLogger("signalforge.ingest")

REJECTED_TOPIC = "system.ingest.rejected"
AUDIT_REJECTED = "audit.ingest.rejected"
AUDIT_DUPLICATE = "audit.ingest.duplicate"

MAX_AUDITED_DETAILS = 20
"""An audit entry names the first few reasons rather than all five hundred: the
chain is permanent, and a 500-item rejection would put a sender's whole batch in
it every time it retried."""

NORMALIZE_CAPABILITY = "normalize"

REFUSAL_CODES = {
    "malformed timestamp": "malformed_timestamp",
    "timestamp outside replay window": "replay_window",
    "signature mismatch": "bad_signature",
    "ingest key revoked": "revoked",
    "key id does not match the presented token": "key_id_mismatch",
    "shared secret missing": "secret_missing",
}
"""The refusal reasons, mapped to a closed vocabulary before any of them becomes
a metric label. /metrics is unauthenticated, so its label values stay a set this
module can enumerate; anything unmapped collapses to `other`."""


@dataclass(frozen=True)
class AuthenticatedIngest:
    """A verified request: which key, which stream, and the exact bytes signed."""

    key: IngestKey
    stream: IngestStream
    body: bytes


@dataclass
class _Outcome:
    accepted: int = 0
    duplicates: int = 0
    rejections: list[IngestRejection] = field(default_factory=list)
    duplicate_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Translation:
    item: IngestItem
    payloads: list[BaseModel]
    adapter_id: str


class IngestService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        bus: EventBus,
        verifier: IngestHmacVerifier,
        rate_limiter: RateLimiter,
        invoker: ConnectorInvoker,
        clock: Clock,
        recording_clock: Clock,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._verifier = verifier
        self._rate_limiter = rate_limiter
        self._invoker = invoker
        self._clock = clock
        # Wall time, never a drill's virtual clock: the replay window and the
        # "when did this actually reach us" stamp are security-relevant, and a
        # 100000x drill clock must not be able to widen or close either.
        self._recording_clock = recording_clock
        self._metrics = metrics

    # -- authentication ------------------------------------------------------

    async def authenticate(
        self,
        *,
        stream: IngestStream,
        authorization: str,
        signature: str,
        timestamp: str,
        key_id: Optional[str],
        body: bytes,
    ) -> IngestKey:
        token = bearer_token(authorization)
        async with self._uow_factory() as uow:
            key = await uow.ingest_keys.find_by_token_hash(hash_ingest_token(token))
            secret = (
                await uow.secrets.get(key.tenant_id, secret_name(key.id))
                if key is not None
                else None
            )
        if key is None:
            # No tenant is known, so there is nothing to audit against - a forged
            # token would otherwise let an anonymous caller choose whose audit
            # chain to write to. The metric and the JSON log are the record.
            self._refusal_metric(stream, "unknown_token")
            logger.warning("ingest token not recognised", extra={"stream": stream.value})
            raise IngestAuthError("unknown ingest token")

        # Before anything expensive, and deliberately before any audit write.
        self._rate_limiter.hit(f"ingest:{key.id}")

        try:
            key.assert_presentable(key_id)
            self._verify(key, secret, timestamp=timestamp, signature=signature, body=body)
            key.assert_stream(stream)
        except (IngestAuthError, IngestStreamError) as exc:
            await self._record_refusal(key, stream, exc.reason)
            raise
        return key

    def _verify(
        self,
        key: IngestKey,
        secret: Optional[str],
        *,
        timestamp: str,
        signature: str,
        body: bytes,
    ) -> None:
        if not timestamp or not signature:
            raise IngestAuthError(f"missing {HDR_TIMESTAMP} or {HDR_SIGNATURE} header")
        if not secret:
            # The key row exists but its secret does not, so no signature could
            # ever verify. Refused rather than treated as a mismatch, because
            # the operator needs to know the key is broken, not the sender.
            raise IngestAuthError("shared secret missing")
        result = self._verifier.verify(
            secret=secret, timestamp=timestamp, body=body, signature=signature
        )
        if not result.ok:
            raise IngestAuthError(result.reason or "signature rejected")

    # -- acceptance ----------------------------------------------------------

    async def accept(
        self, authenticated: AuthenticatedIngest, *, request_id: Optional[str] = None
    ) -> IngestAck:
        key, stream = authenticated.key, authenticated.stream
        source_system, simulated, items = self._parse(authenticated.body)

        normalizer = _Normalizer(self._invoker, key, self._metrics)
        received_at = self._recording_clock.now()
        outcome = _Outcome()

        async with self._uow_factory() as uow:
            claimed = await uow.ingest_receipts.seen(
                key.tenant_id,
                [eid for eid in (item_event_id(raw) for raw in items) if eid],
            )
            for index, raw in enumerate(items):
                await self._handle_item(
                    uow,
                    outcome=outcome,
                    claimed=claimed,
                    index=index,
                    raw=raw,
                    key=key,
                    stream=stream,
                    source_system=source_system,
                    simulated=simulated,
                    received_at=received_at,
                    request_id=request_id,
                    normalizer=normalizer,
                )
            await uow.ingest_keys.update(
                key.observed_batch(
                    received_at,
                    accepted=outcome.accepted,
                    duplicates=outcome.duplicates,
                    rejected=len(outcome.rejections),
                )
            )
            await self._audit_outcome(uow, key, stream, source_system, outcome)
            await uow.commit()

        self._count(stream, "accepted", outcome.accepted)
        self._count(stream, "duplicate", outcome.duplicates)
        self._count(stream, "rejected", len(outcome.rejections))
        return IngestAck(
            accepted=outcome.accepted,
            duplicates=outcome.duplicates,
            rejected=outcome.rejections,
            request_id=request_id,
        )

    async def _handle_item(
        self,
        uow: IdentityUnitOfWork,
        *,
        outcome: _Outcome,
        claimed: set[str],
        index: int,
        raw: Any,
        key: IngestKey,
        stream: IngestStream,
        source_system: str,
        simulated: bool,
        received_at: datetime,
        request_id: Optional[str],
        normalizer: "_Normalizer",
    ) -> None:
        event_id = item_event_id(raw)
        if event_id is not None and event_id in claimed:
            # Checked before validation on purpose: a duplicate that would also
            # fail validation is still a duplicate, so a sender retrying a batch
            # we already recorded gets the same answer every time.
            outcome.duplicates += 1
            outcome.duplicate_ids.append(event_id)
            return

        try:
            item = validate_item(stream, raw)
            payloads: list[BaseModel] = [item.payload]
            normalized_by: Optional[str] = None
        except IngestPayloadError as canonical_error:
            translation = await normalizer.translate(stream, raw)
            if translation is None:
                outcome.rejections.append(
                    IngestRejection(
                        index=index,
                        reason=normalizer.combine(str(canonical_error)),
                    )
                )
                return
            item = translation.item
            payloads = translation.payloads
            normalized_by = translation.adapter_id

        try:
            payloads = [self._check_payload(stream, payload) for payload in payloads]
        except IngestPayloadError as exc:
            outcome.rejections.append(IngestRejection(index=index, reason=str(exc)))
            return

        published = [
            await self._publish(
                uow,
                key=key,
                stream=stream,
                item=item,
                payload=payload,
                source_system=source_system,
                simulated=simulated,
                received_at=received_at,
                request_id=request_id,
            )
            for payload in payloads
        ]
        await uow.ingest_receipts.add(
            IngestReceipt(
                tenant_id=key.tenant_id,
                event_id=str(item.event_id),
                key_id=key.id,
                app_id=key.app_id,
                stream=stream,
                source_system=source_system,
                occurred_at=item.occurred_at,
                received_at=received_at,
                platform_event_id=str(published[0].event_id),
                event_seq=int(published[0].seq or 0),
                event_count=len(published),
                simulated=simulated,
                normalized_by=normalized_by,
            )
        )
        # Two items in one batch carrying the same id: the first claims it and
        # the second is a duplicate, rather than a unique-constraint failure that
        # would cost the whole batch.
        claimed.add(str(item.event_id))
        outcome.accepted += 1

    @staticmethod
    def _check_payload(stream: IngestStream, payload: BaseModel) -> BaseModel:
        """Domain rules a schema cannot express, applied before anything is stored."""
        if stream is IngestStream.TOPOLOGY:
            try:
                assert_consistent(payload)  # type: ignore[arg-type]
            except AdapterError as exc:
                # A graph with half its edges looks complete and understates
                # blast radius, so it is refused whole - the same rule the pull
                # importer applies, in the push direction.
                raise IngestPayloadError(str(exc)) from exc
        return payload

    async def _publish(
        self,
        uow: IdentityUnitOfWork,
        *,
        key: IngestKey,
        stream: IngestStream,
        item: IngestItem,
        payload: BaseModel,
        source_system: str,
        simulated: bool,
        received_at: datetime,
        request_id: Optional[str],
    ) -> EventEnvelope:
        masked, redacted_fields = ingest_redaction.apply(stream, payload)
        return await self._bus.publish(
            uow,
            EventEnvelope(
                topic=topic_for(stream, masked),
                tenant_id=key.tenant_id,
                app_id=key.app_id,
                # Telemetry ages out with the signal plane; what an incident did
                # with it is what gets sealed.
                plane=Plane.SIGNAL,
                occurred_at=item.occurred_at,
                actor=Actor(type=ActorType.EXTERNAL, id=source_system, display=key.name),
                source=Source(
                    system=source_system,
                    kind=stream.adapter_kind,
                    authority=Authority.OBSERVED,
                    freshness_s=max(
                        (received_at - item.occurred_at).total_seconds(), 0.0
                    ),
                    simulated=simulated,
                ),
                correlation_id=request_id,
                # The producer's id travels as the idempotency key rather than as
                # the platform event id: event ids are globally unique here, and
                # two tenants choosing the same producer id must not collide.
                idempotency_key=f"ingest:{stream.value}:{item.event_id}",
                redaction=Redaction(
                    applied=bool(redacted_fields),
                    fields=redacted_fields,
                    policy_id=ingest_redaction.POLICY_ID,
                ),
                payload=json.loads(masked.model_dump_json()),
            ),
        )

    # -- parsing -------------------------------------------------------------

    @staticmethod
    def _parse(body: bytes) -> tuple[str, bool, list[Any]]:
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IngestPayloadError(f"body is not valid JSON: {exc}") from exc
        return validate_envelope(document)

    # -- refusals and evidence ----------------------------------------------

    async def _record_refusal(
        self, key: IngestKey, stream: IngestStream, reason: str
    ) -> None:
        """A refused request against a known key is evidence, not just a log line.

        It lands in the tenant's audit chain and on its timeline, and the reason
        is written to the key so the operator who holds manage_apps can read it
        on `GET /ingest-keys` - the sender itself only ever gets a bare 401.
        """
        code = REFUSAL_CODES.get(reason, "other")
        self._refusal_metric(stream, code)
        now = self._recording_clock.now()
        payload = {
            "key_id": key.id,
            "app_id": key.app_id,
            "stream": stream.value,
            "phase": "request",
            "reason": reason,
            "code": code,
            "token_prefix": key.token_prefix,
        }
        async with self._uow_factory() as uow:
            await uow.ingest_keys.update(key.refused(now, reason))
            await uow.audit.append(
                tenant_id=key.tenant_id,
                topic=AUDIT_REJECTED,
                actor_type="external",
                actor_id=key.token_prefix,
                app_id=key.app_id,
                payload=payload,
            )
            await self._publish_platform(uow, key, payload)
            await uow.commit()
        logger.warning(
            "ingest request refused",
            extra={"stream": stream.value, "reason": reason, "code": code},
        )

    async def _audit_outcome(
        self,
        uow: IdentityUnitOfWork,
        key: IngestKey,
        stream: IngestStream,
        source_system: str,
        outcome: _Outcome,
    ) -> None:
        if outcome.duplicates:
            await uow.audit.append(
                tenant_id=key.tenant_id,
                topic=AUDIT_DUPLICATE,
                actor_type="external",
                actor_id=source_system,
                app_id=key.app_id,
                payload={
                    "key_id": key.id,
                    "stream": stream.value,
                    "source_system": source_system,
                    "count": outcome.duplicates,
                    "event_ids": outcome.duplicate_ids[:MAX_AUDITED_DETAILS],
                },
            )
        if outcome.rejections:
            payload = {
                "key_id": key.id,
                "app_id": key.app_id,
                "stream": stream.value,
                "phase": "items",
                "source_system": source_system,
                "count": len(outcome.rejections),
                "rejected": [
                    {"index": r.index, "reason": r.reason}
                    for r in outcome.rejections[:MAX_AUDITED_DETAILS]
                ],
            }
            await uow.audit.append(
                tenant_id=key.tenant_id,
                topic=AUDIT_REJECTED,
                actor_type="external",
                actor_id=source_system,
                app_id=key.app_id,
                payload=payload,
            )
            await self._publish_platform(uow, key, payload)

    async def _publish_platform(
        self, uow: IdentityUnitOfWork, key: IngestKey, payload: dict[str, Any]
    ) -> None:
        await self._bus.publish(
            uow,
            EventEnvelope(
                topic=REJECTED_TOPIC,
                tenant_id=key.tenant_id,
                app_id=key.app_id,
                plane=Plane.SIGNAL,
                occurred_at=self._clock.now(),
                actor=Actor(type=ActorType.SYSTEM, id="ingest-api"),
                source=Source(
                    system="ingest-api",
                    kind=key.stream.adapter_kind,
                    authority=Authority.AUTHORITATIVE,
                ),
                payload=dict(payload),
            ),
        )

    # -- metrics -------------------------------------------------------------

    def _refusal_metric(self, stream: IngestStream, code: str) -> None:
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_ingest_refused_total",
                {"stream": stream.value, "reason": code},
            )

    def _count(self, stream: IngestStream, outcome: str, value: int) -> None:
        if self._metrics is not None and value:
            self._metrics.increment(
                "signalforge_ingest_items_total",
                {"stream": stream.value, "outcome": outcome},
                value=value,
            )


class _Normalizer:
    """The bridge from a vendor-shaped payload to the canonical model.

    A sender that already speaks the canonical contract never reaches this. When
    one does not - a Grafana webhook body carried inside an ingest item - the
    key's connector binding names the adapter that translates it, and the
    adapter is resolved **once per batch**: `ConnectorInvoker.resolve` re-reads
    the binding, re-checks the credential-scope floor and decrypts credentials,
    and doing that five hundred times for one POST would cost more than the
    events it produces.

    Nothing here is fatal to the request. A binding disabled between two posts,
    an adapter uninstalled under it, or a payload the adapter refuses, all reject
    the items that needed translating and name why; the canonical items in the
    same batch still land.
    """

    def __init__(self, invoker: ConnectorInvoker, key: IngestKey, metrics):
        self._invoker = invoker
        self._key = key
        self._metrics = metrics
        self._resolved = None
        self._attempted = False
        self._resolve_error: Optional[str] = None
        self._item_error: Optional[str] = None

    async def translate(self, stream: IngestStream, raw: Any) -> Optional[_Translation]:
        self._item_error = None
        if self._key.binding_id is None or not isinstance(raw, Mapping):
            return None
        resolved = await self._resolve()
        if resolved is None:
            return None
        payload = raw.get("payload")
        if not isinstance(payload, Mapping):
            return None
        try:
            produced = resolved.adapter.normalize(dict(payload))
            self._record(resolved, "ok")
        except AdapterError as exc:
            self._record(resolved, "error")
            self._item_error = str(exc)
            return None
        except Exception:
            # An adapter raising outside the frozen taxonomy is a bug in that
            # adapter, counted separately so it is visible as something other
            # than "the payload was bad".
            self._record(resolved, "untyped_error")
            logger.warning(
                "ingest normaliser raised outside the error taxonomy",
                extra={"adapter": resolved.manifest.id, "stream": stream.value},
            )
            self._item_error = f"{resolved.manifest.id} failed to normalise the payload"
            return None

        models = list(produced) if isinstance(produced, list) else [produced]
        if not models:
            self._item_error = f"{resolved.manifest.id} produced no {stream.value}"
            return None
        try:
            # Re-validated through the frozen contract shape rather than trusted:
            # the wrapper's event_id and occurred_at still have to be right, and
            # an adapter's output is checked exactly as a sender's would be.
            rebuilt = validate_item(
                stream, {**raw, "payload": json.loads(models[0].model_dump_json())}
            )
            payloads = [rebuilt.payload] + [
                validate_payload(stream, json.loads(model.model_dump_json()))
                for model in models[1:]
            ]
        except IngestPayloadError as exc:
            self._item_error = str(exc)
            return None
        return _Translation(
            item=rebuilt, payloads=payloads, adapter_id=resolved.manifest.id
        )

    def combine(self, canonical_reason: str) -> str:
        """The reason an item is rejected: what the canonical contract said, plus
        why the adapter could not rescue it when one was configured."""
        detail = self._item_error or self._resolve_error
        if self._key.binding_id is None or detail is None:
            return canonical_reason
        return f"{canonical_reason}; normaliser: {detail}"

    async def _resolve(self):
        if self._attempted:
            return self._resolved
        self._attempted = True
        try:
            self._resolved = await self._invoker.resolve(
                self._key.tenant_id, self._key.binding_id, NORMALIZE_CAPABILITY
            )
        except Exception as exc:
            # Includes a disabled binding, an uninstalled adapter, a missing
            # runtime and a manifest that stopped declaring normalize.
            self._resolve_error = str(exc)
            self._resolved = None
        return self._resolved

    def _record(self, resolved, outcome: str) -> None:
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_adapter_calls_total",
                {
                    "adapter": resolved.manifest.id,
                    "capability": NORMALIZE_CAPABILITY,
                    "outcome": outcome,
                },
            )
