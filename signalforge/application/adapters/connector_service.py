"""Per-app connector bindings (REQ-030, REQ-015, REQ-013).

Binding an application to an adapter is four writes that have to be one commit:
the binding row, the Fernet-encrypted credentials, the audit entry and the event.
They share the identity unit of work for exactly that reason - a credential
stored under a binding that does not exist is unreachable key material, and a
binding whose credential write failed is a connector that silently cannot
authenticate. Either both land or neither does.

Three rules are enforced here rather than at the edge:

- The credential scope must be one the adapter's manifest offers, and the
  manifest already refuses to offer read_only for anything that can mutate. So
  "read-only credentials cannot drive remediation" is true by construction,
  before any policy exists to say so.
- Config is validated against the manifest's own config_schema, once, at write
  time. A wizard renders fields from that schema; a bad value never reaches a
  poller.
- One binding per (app, adapter, credential scope). Read-only and remediation
  are separate credentials with separate blast radii, so they are separate
  bindings; a second binding at the same scope is a duplicate and is refused.

Nothing in this module returns, logs or audits a credential value. Bindings
carry the *names* of their secrets; the values are only ever decrypted on the
path to constructing an adapter instance.
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
from signalforge.domain.adapters.bindings import (
    ConnectorBinding,
    assert_scope_offered,
    secret_name,
    validate_config,
)
from signalforge.domain.adapters.errors import (
    ConnectorBindingNotFoundError,
    ConnectorConfigError,
    DuplicateBindingError,
)
from signalforge.domain.adapters.manifest import AdapterManifest, CredentialScope
from signalforge.domain.adapters.ports import AdapterRegistry
from signalforge.domain.identity.ports import Clock

BOUND_TOPIC = "system.connector.bound"
UPDATED_TOPIC = "system.connector.updated"
UNBOUND_TOPIC = "system.connector.unbound"


@dataclass(frozen=True)
class ConnectorView:
    """A binding with its manifest resolved. `manifest` is None when the adapter
    folder has been removed from a deployment that still holds bindings for it -
    an orphan, which the health loop reports DOWN and the UI shows in red rather
    than dropping from the list."""

    binding: ConnectorBinding
    manifest: Optional[AdapterManifest]

    @property
    def config(self) -> dict[str, Any]:
        """Config as it may leave the process: the manifest's declared redaction
        fields masked."""
        if self.manifest is None:
            return dict(self.binding.config)
        return self.manifest.redact(self.binding.config)


class ConnectorService:
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

    # -- reads -------------------------------------------------------------

    async def list(
        self,
        principal: Principal,
        *,
        app_id: Optional[str] = None,
        adapter_id: Optional[str] = None,
        enabled_only: bool = False,
    ) -> Sequence[ConnectorView]:
        async with self._uow_factory() as uow:
            bindings = await uow.connectors.list(
                principal.tenant_id,
                app_id=app_id,
                adapter_id=adapter_id,
                enabled_only=enabled_only,
            )
        return [self._view(binding) for binding in bindings]

    async def get(self, principal: Principal, binding_id: str) -> ConnectorView:
        async with self._uow_factory() as uow:
            binding = await self._require(uow, principal.tenant_id, binding_id)
        return self._view(binding)

    # -- writes ------------------------------------------------------------

    async def create(
        self,
        principal: Principal,
        *,
        app_id: str,
        adapter_id: str,
        name: str,
        credential_scope: CredentialScope,
        config: Mapping[str, Any],
        credentials: Mapping[str, str],
        enabled: bool = True,
    ) -> ConnectorView:
        manifest = self._registry.get(adapter_id)
        assert_scope_offered(manifest, credential_scope)
        checked_config = validate_config(manifest, config)
        self._check_credentials(manifest, credentials)

        now = self._clock.now()
        binding = ConnectorBinding(
            id=str(uuid.uuid4()),
            tenant_id=principal.tenant_id,
            app_id=app_id,
            adapter_id=adapter_id,
            name=name,
            credential_scope=credential_scope,
            config=checked_config,
            credential_fields=tuple(manifest.auth.credential_fields),
            enabled=enabled,
            created_at=now,
            created_by=principal.user_id,
            updated_at=now,
        )

        async with self._uow_factory() as uow:
            existing = await uow.connectors.find_conflict(
                principal.tenant_id,
                app_id=app_id,
                adapter_id=adapter_id,
                scope=credential_scope,
            )
            if existing is not None:
                raise DuplicateBindingError(
                    f"{app_id} already binds {adapter_id} at scope "
                    f"{credential_scope.value}; update or delete binding {existing.id}"
                )
            await uow.connectors.add(binding)
            await self._store_credentials(uow, binding, credentials)
            payload = self._payload(binding, manifest)
            await uow.audit.append(
                tenant_id=binding.tenant_id,
                topic=BOUND_TOPIC,
                actor_type="human",
                actor_id=principal.user_id,
                app_id=app_id,
                payload=payload,
            )
            await self._publish(
                uow, binding, manifest, BOUND_TOPIC, payload, actor_id=principal.user_id
            )
            await uow.commit()
        return ConnectorView(binding=binding, manifest=manifest)

    async def update(
        self,
        principal: Principal,
        binding_id: str,
        *,
        config: Optional[Mapping[str, Any]] = None,
        credentials: Optional[Mapping[str, str]] = None,
        enabled: Optional[bool] = None,
    ) -> ConnectorView:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            binding = await self._require(uow, principal.tenant_id, binding_id)
            manifest = self._registry.get(binding.adapter_id)

            changed: list[str] = []
            if config is not None:
                binding = binding.with_config(validate_config(manifest, config), at=now)
                changed.append("config")
            if enabled is not None and enabled != binding.enabled:
                binding = binding.with_enabled(enabled, at=now)
                changed.append("enabled")
            if credentials is not None:
                self._check_credentials(manifest, credentials)
                await self._store_credentials(uow, binding, credentials)
                changed.append("credentials")
            if not changed:
                return ConnectorView(binding=binding, manifest=manifest)

            await uow.connectors.update(binding)
            payload = self._payload(binding, manifest) | {"changed": sorted(changed)}
            await uow.audit.append(
                tenant_id=binding.tenant_id,
                topic=UPDATED_TOPIC,
                actor_type="human",
                actor_id=principal.user_id,
                app_id=binding.app_id,
                payload=payload,
            )
            await self._publish(
                uow, binding, manifest, UPDATED_TOPIC, payload, actor_id=principal.user_id
            )
            await uow.commit()
        return ConnectorView(binding=binding, manifest=manifest)

    async def delete(self, principal: Principal, binding_id: str) -> None:
        async with self._uow_factory() as uow:
            binding = await self._require(uow, principal.tenant_id, binding_id)
            manifest = self._registry.find(binding.adapter_id)
            # Credentials go with the binding. Encoding the binding id in the
            # secret name is what makes this exact rather than a prefix sweep.
            for credential_field in binding.credential_fields:
                await uow.secrets.delete(
                    binding.tenant_id, secret_name(binding.id, credential_field)
                )
            await uow.connectors.delete(binding.tenant_id, binding.id)
            payload = self._payload(binding, manifest)
            await uow.audit.append(
                tenant_id=binding.tenant_id,
                topic=UNBOUND_TOPIC,
                actor_type="human",
                actor_id=principal.user_id,
                app_id=binding.app_id,
                payload=payload,
            )
            await self._publish(
                uow, binding, manifest, UNBOUND_TOPIC, payload, actor_id=principal.user_id
            )
            await uow.commit()

    # -- credentials -------------------------------------------------------

    async def credentials_for(self, binding: ConnectorBinding) -> dict[str, str]:
        """Decrypt this binding's credentials. The only path from ciphertext to
        plaintext, and its result goes straight into an AdapterContext - which
        redacts itself in reprs - and nowhere else."""
        async with self._uow_factory() as uow:
            values: dict[str, str] = {}
            for credential_field in binding.credential_fields:
                value = await uow.secrets.get(
                    binding.tenant_id, secret_name(binding.id, credential_field)
                )
                if value is not None:
                    values[credential_field] = value
            return values

    async def _store_credentials(
        self,
        uow: IdentityUnitOfWork,
        binding: ConnectorBinding,
        credentials: Mapping[str, str],
    ) -> None:
        for credential_field in binding.credential_fields:
            await uow.secrets.put(
                binding.tenant_id,
                secret_name(binding.id, credential_field),
                credentials[credential_field],
            )

    @staticmethod
    def _check_credentials(
        manifest: AdapterManifest, credentials: Mapping[str, str]
    ) -> None:
        expected = set(manifest.auth.credential_fields)
        given = set(credentials)
        missing = sorted(expected - given)
        unexpected = sorted(given - expected)
        if missing:
            raise ConnectorConfigError(
                f"adapter {manifest.id} requires credentials {missing}"
            )
        if unexpected:
            raise ConnectorConfigError(
                f"adapter {manifest.id} does not use credentials {unexpected}"
            )
        blank = sorted(name for name, value in credentials.items() if not str(value).strip())
        if blank:
            raise ConnectorConfigError(f"credentials {blank} must not be empty")

    # -- internals ---------------------------------------------------------

    async def _require(
        self, uow: IdentityUnitOfWork, tenant_id: str, binding_id: str
    ) -> ConnectorBinding:
        binding = await uow.connectors.get(tenant_id, binding_id)
        if binding is None:
            # Same message whether it is another tenant's binding or nobody's:
            # a 404 must not confirm that an id exists elsewhere (REQ-013).
            raise ConnectorBindingNotFoundError(f"connector {binding_id} not found")
        return binding

    def _view(self, binding: ConnectorBinding) -> ConnectorView:
        return ConnectorView(
            binding=binding, manifest=self._registry.find(binding.adapter_id)
        )

    @staticmethod
    def _payload(
        binding: ConnectorBinding, manifest: Optional[AdapterManifest]
    ) -> dict[str, Any]:
        config = manifest.redact(binding.config) if manifest else dict(binding.config)
        return {
            "binding_id": binding.id,
            "app_id": binding.app_id,
            "adapter_id": binding.adapter_id,
            "kind": manifest.kind.value if manifest else None,
            "mode": manifest.mode.value if manifest else None,
            "credential_scope": binding.credential_scope.value,
            "enabled": binding.enabled,
            "simulated": bool(manifest and manifest.simulated),
            "config": config,
            # Names only. A credential value has never been in this dict and the
            # audit chain is permanent, so it never can be.
            "credential_fields": list(binding.credential_fields),
        }

    async def _publish(
        self,
        uow: IdentityUnitOfWork,
        binding: ConnectorBinding,
        manifest: Optional[AdapterManifest],
        topic: str,
        payload: Mapping[str, Any],
        *,
        actor_id: str,
    ) -> None:
        await self._bus.publish(
            uow,
            EventEnvelope(
                topic=topic,
                tenant_id=binding.tenant_id,
                app_id=binding.app_id,
                # Configuration facts age out with the signal plane; what an
                # incident did with a connector is what gets sealed, not how the
                # connector was wired six months earlier.
                plane=Plane.SIGNAL,
                occurred_at=self._clock.now(),
                actor=Actor(type=ActorType.HUMAN, id=actor_id),
                source=Source(
                    system="adapter-registry",
                    kind=manifest.kind if manifest else None,
                    authority=Authority.AUTHORITATIVE,
                    simulated=bool(manifest and manifest.simulated),
                ),
                payload=dict(payload),
            ),
        )
