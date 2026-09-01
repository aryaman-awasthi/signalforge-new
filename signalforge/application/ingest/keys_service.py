"""Issuing, rotating and revoking ingest keys (REQ-032, REQ-015, REQ-012).

Issuing a key is three writes that have to be one commit - the key row, the
Fernet-encrypted shared secret and the audit entry - for the same reason a
connector binding is: a secret stored under a key that does not exist is
unreachable key material, and a key whose secret write failed is a credential
that can never produce a valid signature.

The token and the secret are returned exactly once, in the 201. Nothing stores
the token (only its sha256) and nothing reads the secret back out to an
operator, so "I lost it" is answered by rotating, not by looking it up. That is
deliberate: a platform that can show you a credential can show it to whoever
takes over your session.

Rotation issues a *successor* rather than overwriting: both keys are live, each
with its own secret, until the operator revokes the old one. A sender cannot be
switched atomically - it is a config change in somebody else's system - so an
overwrite would drop every request in flight between the two deploys.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from signalforge.application.events.bus import EventBus
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.contracts.envelope import (
    Actor,
    ActorType,
    Authority,
    EventEnvelope,
    Plane,
    Source,
)
from signalforge.domain.adapters.manifest import AdapterMode
from signalforge.domain.adapters.ports import AdapterRegistry
from signalforge.domain.identity.ports import Clock
from signalforge.domain.ingest.errors import IngestKeyConfigError, IngestKeyNotFoundError
from signalforge.domain.ingest.keys import (
    IngestKey,
    display_prefix,
    hash_ingest_token,
    new_ingest_secret,
    new_ingest_token,
    secret_name,
)
from signalforge.domain.ingest.streams import IngestStream

ISSUED_TOPIC = "system.ingest.key.issued"
ROTATED_TOPIC = "system.ingest.key.rotated"
REVOKED_TOPIC = "system.ingest.key.revoked"

NORMALIZE_CAPABILITY = "normalize"


@dataclass(frozen=True)
class IssuedKey:
    """The one and only time the credential material exists outside storage."""

    key: IngestKey
    token: str
    secret: str

    def __repr__(self) -> str:  # never let it reach a log or a traceback
        return f"IssuedKey(key={self.key.id!r}, token=<redacted>, secret=<redacted>)"


class IngestKeyService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        registry: AdapterRegistry,
        bus: EventBus,
        clock: Clock,
    ):
        self._uow_factory = uow_factory
        self._registry = registry
        self._bus = bus
        self._clock = clock

    # -- reads ---------------------------------------------------------------

    async def list(
        self,
        principal: Principal,
        *,
        app_id: Optional[str] = None,
        stream: Optional[IngestStream] = None,
        include_revoked: bool = False,
    ) -> Sequence[IngestKey]:
        async with self._uow_factory() as uow:
            return await uow.ingest_keys.list(
                principal.tenant_id,
                app_id=app_id,
                stream=stream,
                include_revoked=include_revoked,
            )

    async def get(self, principal: Principal, key_id: str) -> IngestKey:
        async with self._uow_factory() as uow:
            return await self._require(uow, principal.tenant_id, key_id)

    # -- writes --------------------------------------------------------------

    async def issue(
        self,
        principal: Principal,
        *,
        app_id: str,
        stream: IngestStream,
        name: str,
        binding_id: Optional[str] = None,
    ) -> IssuedKey:
        async with self._uow_factory() as uow:
            await self._check_binding(uow, principal.tenant_id, stream, binding_id)
            issued = self._mint(
                tenant_id=principal.tenant_id,
                app_id=app_id,
                stream=stream,
                name=name,
                binding_id=binding_id,
                created_by=principal.user_id,
            )
            await self._store(uow, issued, principal, ISSUED_TOPIC)
            await uow.commit()
        return issued

    async def rotate(self, principal: Principal, key_id: str) -> IssuedKey:
        async with self._uow_factory() as uow:
            existing = await self._require(uow, principal.tenant_id, key_id)
            if not existing.active:
                raise IngestKeyConfigError(
                    f"ingest key {key_id} is revoked; issue a new one instead"
                )
            issued = self._mint(
                tenant_id=existing.tenant_id,
                app_id=existing.app_id,
                stream=existing.stream,
                name=_successor_name(existing.name),
                binding_id=existing.binding_id,
                created_by=principal.user_id,
                rotated_from=existing.id,
            )
            await self._store(uow, issued, principal, ROTATED_TOPIC)
            await uow.commit()
        return issued

    async def revoke(self, principal: Principal, key_id: str) -> IngestKey:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            existing = await self._require(uow, principal.tenant_id, key_id)
            revoked = existing.revoked(now, principal.user_id)
            await uow.ingest_keys.update(revoked)
            # The shared secret goes with the key. A revoked key that could
            # still produce a valid signature would only be revoked on paper.
            await uow.secrets.delete(existing.tenant_id, secret_name(existing.id))
            payload = _payload(revoked)
            await uow.audit.append(
                tenant_id=revoked.tenant_id,
                topic=REVOKED_TOPIC,
                actor_type="human",
                actor_id=principal.user_id,
                app_id=revoked.app_id,
                payload=payload,
            )
            await self._publish(uow, revoked, REVOKED_TOPIC, payload, principal.user_id)
            await uow.commit()
        return revoked

    # -- internals -----------------------------------------------------------

    def _mint(
        self,
        *,
        tenant_id: str,
        app_id: str,
        stream: IngestStream,
        name: str,
        binding_id: Optional[str],
        created_by: str,
        rotated_from: Optional[str] = None,
    ) -> IssuedKey:
        token = new_ingest_token()
        key = IngestKey(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            app_id=app_id,
            stream=stream,
            name=name,
            token_hash=hash_ingest_token(token),
            token_prefix=display_prefix(token),
            binding_id=binding_id,
            rotated_from=rotated_from,
            created_at=self._clock.now(),
            created_by=created_by,
        )
        return IssuedKey(key=key, token=token, secret=new_ingest_secret())

    async def _store(
        self,
        uow: IdentityUnitOfWork,
        issued: IssuedKey,
        principal: Principal,
        topic: str,
    ) -> None:
        await uow.ingest_keys.add(issued.key)
        await uow.secrets.put(
            issued.key.tenant_id, secret_name(issued.key.id), issued.secret
        )
        payload = _payload(issued.key)
        await uow.audit.append(
            tenant_id=issued.key.tenant_id,
            topic=topic,
            actor_type="human",
            actor_id=principal.user_id,
            app_id=issued.key.app_id,
            payload=payload,
        )
        await self._publish(uow, issued.key, topic, payload, principal.user_id)

    async def _check_binding(
        self,
        uow: IdentityUnitOfWork,
        tenant_id: str,
        stream: IngestStream,
        binding_id: Optional[str],
    ) -> None:
        """A key may name a connector binding, and then that binding's adapter is
        what translates a vendor-shaped payload into the canonical model. It has
        to be able to: the right kind, push mode, and actually declaring
        `normalize`. Checked here rather than at post time so an operator finds
        out when they wire it, not at 3am when the first alert arrives."""
        if binding_id is None:
            return
        binding = await uow.connectors.get(tenant_id, binding_id)
        if binding is None:
            raise IngestKeyConfigError(f"connector {binding_id} not found")
        manifest = self._registry.find(binding.adapter_id)
        if manifest is None:
            raise IngestKeyConfigError(
                f"adapter {binding.adapter_id!r} is no longer installed"
            )
        if manifest.kind is not stream.adapter_kind:
            raise IngestKeyConfigError(
                f"adapter {manifest.id} is a {manifest.kind.value} adapter and cannot "
                f"normalise the {stream.value} stream"
            )
        if manifest.mode is not AdapterMode.PUSH:
            raise IngestKeyConfigError(
                f"adapter {manifest.id} is a {manifest.mode.value} adapter; only a "
                "push adapter receives on the ingest API"
            )
        if not manifest.supports(NORMALIZE_CAPABILITY):
            raise IngestKeyConfigError(
                f"adapter {manifest.id} does not declare 'normalize'"
            )

    async def _require(
        self, uow: IdentityUnitOfWork, tenant_id: str, key_id: str
    ) -> IngestKey:
        key = await uow.ingest_keys.get(tenant_id, key_id)
        if key is None:
            # Identical message whether it belongs to another tenant or to
            # nobody: a 404 must not confirm that an id exists elsewhere.
            raise IngestKeyNotFoundError(f"ingest key {key_id} not found")
        return key

    async def _publish(
        self,
        uow: IdentityUnitOfWork,
        key: IngestKey,
        topic: str,
        payload: Mapping[str, Any],
        actor_id: str,
    ) -> None:
        await self._bus.publish(
            uow,
            EventEnvelope(
                topic=topic,
                tenant_id=key.tenant_id,
                app_id=key.app_id,
                plane=Plane.SIGNAL,
                occurred_at=self._clock.now(),
                actor=Actor(type=ActorType.HUMAN, id=actor_id),
                source=Source(
                    system="ingest-api",
                    kind=key.stream.adapter_kind,
                    authority=Authority.AUTHORITATIVE,
                ),
                payload=dict(payload),
            ),
        )


ROTATED_SUFFIX = " - rotated"


def _successor_name(name: str) -> str:
    """Names stay inside the key-name character set, and rotating a rotated key
    does not stack suffixes until it overruns the length limit."""
    base = name[:64]
    return base if base.endswith(ROTATED_SUFFIX) else f"{base}{ROTATED_SUFFIX}"


def _payload(key: IngestKey) -> dict[str, Any]:
    """What goes on the timeline and into the audit chain. The prefix identifies
    which key without being usable as one; the token and the secret have never
    been in this dict and the audit chain is permanent, so they never can be."""
    return {
        "key_id": key.id,
        "app_id": key.app_id,
        "stream": key.stream.value,
        "name": key.name,
        "path": key.path,
        "binding_id": key.binding_id,
        "rotated_from": key.rotated_from,
        "token_prefix": key.token_prefix,
        "active": key.active,
    }
