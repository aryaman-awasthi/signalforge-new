"""The wizard steps that write to the onboarding profile (REQ-036).

Map, actions, topology, SLO/severity/policy and baseline. They share a service
because they share a document: the profile's invariants cross the steps - a probe
names a journey, an SLO names a journey, a baseline names a signal - so every
write loads the whole aggregate, applies its section and lets the domain refuse
the result as one thing.

Three behaviours here are load-bearing beyond storage, and all three are about
failure:

- **A refused policy edit is evidence.** Contradicting the floor writes an audit
  entry and a timeline event before the 422 goes back, because "somebody tried
  to make a rollback auto-execute" is exactly the fact an audit wants and the
  error response is seen only by the person who tried.
- **Re-uploading an unchanged topology is a no-op.** Version is content, so the
  second upload produces no version, no event and no change - an operator who is
  not sure whether the first upload worked cannot manufacture a fake map change
  by trying again.
- **Baseline learning completes partially.** Signals whose series were not in
  the scrape are reported by name and the rest are stored, because a run that
  discarded four good baselines over one bad selector would be unusable against
  a real application.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Sequence

from signalforge.application.adapters.invoker import ConnectorInvoker
from signalforge.application.events.bus import EventBus
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.application.onboarding.common import (
    ACTIONS_TOPIC,
    BASELINE_TOPIC,
    GOVERNANCE_TOPIC,
    MAPPING_TOPIC,
    POLICY_REFUSED_TOPIC,
    TOPOLOGY_TOPIC,
    load_profile,
    record,
    require_application,
)
from signalforge.contracts.canonical import (
    ActionDefinition,
    MetricSeries,
    RiskLevel,
    TopologySnapshot,
)
from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.errors import AdapterError
from signalforge.contracts.roles import Role
from signalforge.domain.adapters.bindings import ConnectorBinding
from signalforge.domain.adapters.ports import AdapterRegistry
from signalforge.domain.identity.ports import Clock
from signalforge.domain.integrations.promtext import select
from signalforge.domain.integrations.topology import normalize_document
from signalforge.domain.onboarding.codec import policy_from_mapping, policy_to_mapping
from signalforge.domain.onboarding.errors import (
    OnboardingValidationError,
    PolicyFloorError,
)
from signalforge.domain.onboarding.ports import TopologyVersion
from signalforge.domain.onboarding.profile import (
    MIN_BASELINE_SAMPLES,
    Baseline,
    ExecutionMode,
    Journey,
    MappedAction,
    OnboardingProfile,
    PolicyDefaults,
    ProbeTemplate,
    SeverityMatrix,
    SignalMapping,
    SloTarget,
    TopologyRef,
    action_from_definition,
    summarize,
)


def _require_aware_capture_time(value: object) -> None:
    if isinstance(value, datetime):
        parsed: Optional[datetime] = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            parsed = None  # not a timestamp at all: the snapshot model says so
    if parsed is not None and parsed.tzinfo is None:
        raise OnboardingValidationError(
            "captured_at must be timezone-aware (for example "
            "2026-09-01T11:00:00+00:00)"
        )

MANIFEST_CAPABILITY = "manifest"
NORMALIZE_CAPABILITY = "normalize"
QUERY_INSTANT_CAPABILITY = "query_instant"

ALL_SERIES_SELECTOR = ""
"""One scrape answers every signal. The metrics contract is a point-in-time
exposition, so reading it once per sample and selecting locally costs one HTTP
call instead of one per mapped signal - and every signal in a sample is then
measured at the same instant, which is what makes them comparable."""

DEFAULT_BASELINE_SAMPLES = 5
MAX_BASELINE_SAMPLES = 30

DOMAIN_NORMALIZER = "domain:topology-import"


@dataclass(frozen=True)
class ActionImport:
    profile: OnboardingProfile
    imported: int
    updated: int
    removed: int
    binding_id: str
    adapter_id: str


@dataclass(frozen=True)
class TopologyUpload:
    version: TopologyVersion
    unchanged: bool
    profile: OnboardingProfile


@dataclass(frozen=True)
class BaselineMiss:
    signal_key: str
    reason: str


@dataclass(frozen=True)
class BaselineRun:
    profile: OnboardingProfile
    learned: tuple[Baseline, ...]
    missed: tuple[BaselineMiss, ...]
    samples: int
    window_s: float
    binding_id: str


class OnboardingService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        registry: AdapterRegistry,
        invoker: ConnectorInvoker,
        bus: EventBus,
        clock: Clock,
    ):
        self._uow_factory = uow_factory
        self._registry = registry
        self._invoker = invoker
        self._bus = bus
        self._clock = clock

    # -- map step ----------------------------------------------------------

    async def set_mapping(
        self,
        principal: Principal,
        app_id: str,
        *,
        signals: Optional[Sequence[SignalMapping]] = None,
        region_label: Optional[str] = None,
        regions: Optional[Sequence[str]] = None,
        journeys: Optional[Sequence[Journey]] = None,
        probes: Optional[Sequence[ProbeTemplate]] = None,
        clear_region_label: bool = False,
    ) -> OnboardingProfile:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await require_application(uow, principal.tenant_id, app_id)
            profile = await load_profile(uow, principal.tenant_id, app_id)
            dropped = len(profile.baselines)
            updated = profile.with_mapping(
                at=now,
                signals=signals,
                region_label=region_label,
                regions=regions,
                journeys=journeys,
                probes=probes,
                clear_region_label=clear_region_label,
            )
            await uow.onboarding.put(updated)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=app_id,
                topic=MAPPING_TOPIC,
                payload={
                    "app_id": app_id,
                    "signals": [signal.key for signal in updated.signals],
                    "families": sorted(
                        family.value for family in updated.signal_families
                    ),
                    "region_label": updated.region_label,
                    "regions": list(updated.regions),
                    "journeys": [journey.journey_id for journey in updated.journeys],
                    "probes": [probe.probe_id for probe in updated.probes],
                    "can_corroborate": updated.can_corroborate,
                    "baselines_dropped": max(dropped - len(updated.baselines), 0),
                },
            )
            await uow.commit()
        return updated

    # -- actions -----------------------------------------------------------

    async def import_actions(
        self, principal: Principal, app_id: str, *, binding_id: Optional[str] = None
    ) -> ActionImport:
        async with self._uow_factory() as uow:
            await require_application(uow, principal.tenant_id, app_id)
            binding = await self._binding_for(
                uow,
                principal.tenant_id,
                app_id,
                AdapterKind.ACTIONS,
                MANIFEST_CAPABILITY,
                binding_id,
            )
            profile = await load_profile(uow, principal.tenant_id, app_id)

        definitions: Sequence[ActionDefinition] = await self._invoker.invoke(
            principal.tenant_id, binding.id, MANIFEST_CAPABILITY
        )

        now = self._clock.now()
        existing = {action.action_id: action for action in profile.actions}
        merged: list[MappedAction] = []
        imported = updated = 0
        for definition in definitions:
            fresh = action_from_definition(definition, imported_at=now)
            previous = existing.get(fresh.action_id)
            if previous is None:
                imported += 1
                merged.append(fresh)
                continue
            # A re-import refreshes what the application declares and keeps what
            # the operator decided. Overwriting the override would silently undo
            # a risk decision every time somebody redeployed the manifest.
            updated += 1
            merged.append(
                MappedAction(
                    action_id=fresh.action_id,
                    name=fresh.name,
                    action_class=fresh.action_class,
                    manifest_risk=fresh.manifest_risk,
                    risk_override=_surviving_override(previous, fresh),
                    approver_roles=previous.approver_roles or fresh.approver_roles,
                    execution_mode=_surviving_mode(previous, fresh),
                    reversible=fresh.reversible,
                    reverse_action_id=fresh.reverse_action_id,
                    idempotent=fresh.idempotent,
                    enabled=previous.enabled,
                    description=fresh.description,
                    # The application's own declarations, always refreshed: an
                    # argument schema and a verification list are facts about the
                    # manifest, not operator decisions to preserve (S6-T02).
                    params_schema=fresh.params_schema,
                    verification=fresh.verification,
                    preconditions=fresh.preconditions,
                    imported_at=now,
                )
            )
        removed = sorted(set(existing) - {action.action_id for action in merged})

        async with self._uow_factory() as uow:
            profile = await load_profile(uow, principal.tenant_id, app_id)
            result = profile.with_actions(merged, at=now)
            await uow.onboarding.put(result)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=app_id,
                topic=ACTIONS_TOPIC,
                payload={
                    "app_id": app_id,
                    "adapter_id": binding.adapter_id,
                    "binding_id": binding.id,
                    "imported": imported,
                    "refreshed": updated,
                    "withdrawn": removed,
                    "high_risk": [
                        action.action_id for action in result.high_risk_actions
                    ],
                },
            )
            await uow.commit()
        return ActionImport(
            profile=result,
            imported=imported,
            updated=updated,
            removed=len(removed),
            binding_id=binding.id,
            adapter_id=binding.adapter_id,
        )

    async def override_action(
        self,
        principal: Principal,
        app_id: str,
        action_id: str,
        *,
        risk_override: Optional[RiskLevel] = None,
        clear_risk_override: bool = False,
        approver_roles: Optional[Sequence[Role]] = None,
        execution_mode: Optional[ExecutionMode] = None,
        enabled: Optional[bool] = None,
    ) -> OnboardingProfile:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await require_application(uow, principal.tenant_id, app_id)
            profile = await load_profile(uow, principal.tenant_id, app_id)
            current = profile.action(action_id)
            if current is None:
                raise OnboardingValidationError(
                    f"action {action_id!r} is not imported for {app_id}; run the "
                    "actions import first"
                )
            try:
                changed = current.overridden(
                    risk_override=risk_override,
                    clear_risk_override=clear_risk_override,
                    approver_roles=approver_roles,
                    execution_mode=execution_mode,
                    enabled=enabled,
                )
            except PolicyFloorError as exc:
                await self._audit_refusal(uow, principal, app_id, action_id, exc)
                await uow.commit()
                raise
            updated = profile.with_actions(
                [changed if a.action_id == action_id else a for a in profile.actions],
                at=now,
            )
            await uow.onboarding.put(updated)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=app_id,
                topic=ACTIONS_TOPIC,
                payload={
                    "app_id": app_id,
                    "action_id": action_id,
                    "action_class": changed.action_class.value,
                    "manifest_risk": changed.manifest_risk.value,
                    "effective_risk": changed.effective_risk.value,
                    "execution_mode": changed.execution_mode.value,
                    "approver_roles": [role.value for role in changed.approver_roles],
                    "enabled": changed.enabled,
                    "high_risk": changed.high_risk,
                },
            )
            await uow.commit()
        return updated

    # -- topology ----------------------------------------------------------

    async def upload_topology(
        self,
        principal: Principal,
        app_id: str,
        document: Mapping[str, Any],
        *,
        binding_id: Optional[str] = None,
    ) -> TopologyUpload:
        async with self._uow_factory() as uow:
            await require_application(uow, principal.tenant_id, app_id)
            binding = await self._optional_binding_for(
                uow,
                principal.tenant_id,
                app_id,
                AdapterKind.TOPOLOGY,
                NORMALIZE_CAPABILITY,
                binding_id,
            )

        # An uploaded map is `nodes`, `edges` and an optional `version` - that
        # is what the screen promises. The snapshot contract also wants a
        # capture time, and for a document somebody pasted the honest value is
        # the moment it was uploaded. A document that declares its own is kept.
        document = dict(document)
        if not document.get("captured_at"):
            document["captured_at"] = self._clock.now().isoformat()
        else:
            # The profile codec refuses a naive timestamp on *read*, so one
            # stored here would make the whole application list unreadable
            # (GET /apps) long after this upload answered 200. Refused now,
            # where the person who typed it can see the answer.
            _require_aware_capture_time(document["captured_at"])

        try:
            if binding is not None:
                # The bound adapter is the translator when there is one, so an
                # upload and a scheduled pull produce the same snapshot from the
                # same content - which is what makes versions comparable.
                snapshot: TopologySnapshot = await self._invoker.invoke(
                    principal.tenant_id, binding.id, NORMALIZE_CAPABILITY, document
                )
                normalized_by = binding.adapter_id
            else:
                snapshot = normalize_document(document)
                normalized_by = DOMAIN_NORMALIZER
        except AdapterError as exc:
            # `normalize` is pure: it touches no integrated system, so a failure
            # here is about the *document the operator uploaded*, not about a
            # target being down. It is the caller's 422, not a bad gateway.
            raise OnboardingValidationError(str(exc)) from exc

        now = self._clock.now()
        async with self._uow_factory() as uow:
            existing = await uow.topologies.find(
                principal.tenant_id, app_id, snapshot.version
            )
            if existing is not None:
                profile = await load_profile(uow, principal.tenant_id, app_id)
                return TopologyUpload(
                    version=existing, unchanged=True, profile=profile
                )
            version = TopologyVersion(
                tenant_id=principal.tenant_id,
                app_id=app_id,
                version=snapshot.version,
                snapshot=snapshot,
                imported_at=now,
                imported_by=principal.user_id,
                source="upload",
                normalized_by=normalized_by,
            )
            await uow.topologies.add(version)
            profile = await load_profile(uow, principal.tenant_id, app_id)
            updated = profile.with_topology(
                TopologyRef(
                    version=snapshot.version,
                    captured_at=snapshot.captured_at,
                    node_count=version.node_count,
                    edge_count=version.edge_count,
                    imported_at=now,
                    normalized_by=normalized_by,
                    source="upload",
                ),
                at=now,
            )
            await uow.onboarding.put(updated)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=app_id,
                topic=TOPOLOGY_TOPIC,
                payload={
                    "app_id": app_id,
                    "version": snapshot.version,
                    "captured_at": snapshot.captured_at.isoformat(),
                    "nodes": version.node_count,
                    "edges": version.edge_count,
                    "source": "upload",
                    "normalized_by": normalized_by,
                },
            )
            await uow.commit()
        return TopologyUpload(version=version, unchanged=False, profile=updated)

    async def topology_versions(
        self, principal: Principal, app_id: str, *, limit: int = 50
    ) -> tuple[Optional[TopologyVersion], Sequence[TopologyVersion]]:
        async with self._uow_factory() as uow:
            await require_application(uow, principal.tenant_id, app_id)
            latest = await uow.topologies.latest(principal.tenant_id, app_id)
            versions = await uow.topologies.list(
                principal.tenant_id, app_id, limit=limit
            )
        return latest, versions

    # -- SLOs, severity and policy ------------------------------------------

    async def set_governance(
        self,
        principal: Principal,
        app_id: str,
        *,
        slos: Optional[Sequence[SloTarget]] = None,
        severity_matrix: Optional[SeverityMatrix] = None,
        policy_patch: Optional[Mapping[str, Any]] = None,
    ) -> OnboardingProfile:
        """SLOs and the severity matrix are replaced whole - each is one thing
        an operator edits as a unit - while the policy is *patched*, because a
        wizard field that quietly reset the other six policy values every time
        somebody changed the approval TTL would be a footgun aimed at the floor.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await require_application(uow, principal.tenant_id, app_id)
            profile = await load_profile(uow, principal.tenant_id, app_id)
            policy: Optional[PolicyDefaults] = None
            if policy_patch is not None:
                merged = dict(policy_to_mapping(profile.policy)) | dict(policy_patch)
                try:
                    policy = policy_from_mapping(merged)
                except PolicyFloorError as exc:
                    # The refusal is evidence: the 422 is seen only by whoever
                    # tried, and "somebody tried to widen the floor" is exactly
                    # what an audit wants to hold.
                    await self._audit_refusal(uow, principal, app_id, None, exc)
                    await uow.commit()
                    raise
            updated = profile.with_governance(
                at=now,
                slos=slos,
                severity_matrix=severity_matrix,
                policy=policy,
            )
            await uow.onboarding.put(updated)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=app_id,
                topic=GOVERNANCE_TOPIC,
                payload={
                    "app_id": app_id,
                    "slos": [slo.slo_id for slo in updated.slos],
                    "severity_rules": [
                        rule.severity.value for rule in updated.severity_matrix.rules
                    ],
                    "approval_ttl_s": updated.policy.approval_ttl_s,
                    "stability_window_s": updated.policy.stability_window_s,
                    "auto_execute_low_risk": updated.policy.auto_execute_low_risk,
                    "require_approval_classes": sorted(
                        action_class.value
                        for action_class in updated.policy.require_approval_classes
                    ),
                },
            )
            await uow.commit()
        return updated

    async def _audit_refusal(
        self,
        uow: IdentityUnitOfWork,
        principal: Principal,
        app_id: str,
        action_id: Optional[str],
        error: Exception,
    ) -> None:
        await record(
            uow,
            self._bus,
            self._clock,
            principal=principal,
            app_id=app_id,
            topic=POLICY_REFUSED_TOPIC,
            payload={
                "app_id": app_id,
                "action_id": action_id,
                "reason": str(error)[:400],
                "floor": True,
            },
        )

    # -- baseline ----------------------------------------------------------

    async def learn_baselines(
        self,
        principal: Principal,
        app_id: str,
        *,
        samples: int = DEFAULT_BASELINE_SAMPLES,
        binding_id: Optional[str] = None,
    ) -> BaselineRun:
        count = int(samples)
        if not (MIN_BASELINE_SAMPLES <= count <= MAX_BASELINE_SAMPLES):
            raise OnboardingValidationError(
                f"samples must be between {MIN_BASELINE_SAMPLES} and "
                f"{MAX_BASELINE_SAMPLES}"
            )
        async with self._uow_factory() as uow:
            await require_application(uow, principal.tenant_id, app_id)
            profile = await load_profile(uow, principal.tenant_id, app_id)
            if not profile.signals:
                raise OnboardingValidationError(
                    "map at least one golden signal before learning a baseline; "
                    "there is nothing to measure"
                )
            binding = await self._binding_for(
                uow,
                principal.tenant_id,
                app_id,
                AdapterKind.METRICS,
                QUERY_INSTANT_CAPABILITY,
                binding_id,
            )

        started = self._clock.now()
        scrapes: list[list[MetricSeries]] = []
        for _ in range(count):
            at = self._clock.now()
            scrapes.append(
                await self._invoker.invoke(
                    principal.tenant_id,
                    binding.id,
                    QUERY_INSTANT_CAPABILITY,
                    ALL_SERIES_SELECTOR,
                    at,
                )
            )
        finished = self._clock.now()
        window_s = max((finished - started).total_seconds(), 0.0)

        learned, missed = self._summarize(
            profile, scrapes, window_s=window_s, at=finished, source=binding.adapter_id
        )

        async with self._uow_factory() as uow:
            profile = await load_profile(uow, principal.tenant_id, app_id)
            # Merge, do not replace: a run that could only reach three of five
            # signals must not erase the two it did not touch.
            updated = profile.with_baselines(learned, at=finished)
            await uow.onboarding.put(updated)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=app_id,
                topic=BASELINE_TOPIC,
                payload={
                    "app_id": app_id,
                    "adapter_id": binding.adapter_id,
                    "binding_id": binding.id,
                    "samples": count,
                    "window_s": window_s,
                    "learned": [baseline.signal_key for baseline in learned],
                    "missed": [
                        {"signal_key": miss.signal_key, "reason": miss.reason}
                        for miss in missed
                    ],
                },
            )
            await uow.commit()
        return BaselineRun(
            profile=updated,
            learned=learned,
            missed=missed,
            samples=count,
            window_s=window_s,
            binding_id=binding.id,
        )

    @staticmethod
    def _summarize(
        profile: OnboardingProfile,
        scrapes: Sequence[Sequence[MetricSeries]],
        *,
        window_s: float,
        at: datetime,
        source: str,
    ) -> tuple[tuple[Baseline, ...], tuple[BaselineMiss, ...]]:
        learned: list[Baseline] = []
        missed: list[BaselineMiss] = []
        for signal in profile.signals:
            values: list[float] = []
            series_count = 0
            for scrape in scrapes:
                matched = select(list(scrape), signal.selector)
                series_count = max(series_count, len(matched))
                for series in matched:
                    values.extend(
                        point.value
                        for point in series.points
                        if point.value == point.value  # drop NaN: it is not a sample
                    )
            if len(values) < MIN_BASELINE_SAMPLES:
                missed.append(
                    BaselineMiss(
                        signal_key=signal.key,
                        reason=(
                            f"selector {signal.selector!r} matched {series_count} "
                            f"series and {len(values)} usable samples; at least "
                            f"{MIN_BASELINE_SAMPLES} are needed"
                        ),
                    )
                )
                continue
            stats = summarize(values)
            learned.append(
                Baseline(
                    signal_key=signal.key,
                    samples=len(values),
                    window_s=window_s,
                    series_count=series_count,
                    learned_at=at,
                    source=source,
                    **stats,
                )
            )
        return tuple(learned), tuple(missed)

    # -- binding resolution -------------------------------------------------

    async def _binding_for(
        self,
        uow: IdentityUnitOfWork,
        tenant_id: str,
        app_id: str,
        kind: AdapterKind,
        capability: str,
        binding_id: Optional[str],
    ) -> ConnectorBinding:
        binding = await self._optional_binding_for(
            uow, tenant_id, app_id, kind, capability, binding_id
        )
        if binding is None:
            raise OnboardingValidationError(
                f"{app_id} has no enabled {kind.value} connector that supports "
                f"{capability!r}; connect one in the Connect step first"
            )
        return binding

    async def _optional_binding_for(
        self,
        uow: IdentityUnitOfWork,
        tenant_id: str,
        app_id: str,
        kind: AdapterKind,
        capability: str,
        binding_id: Optional[str],
    ) -> Optional[ConnectorBinding]:
        bindings = await uow.connectors.list(
            tenant_id, app_id=app_id, enabled_only=True
        )
        for binding in bindings:
            if binding_id is not None and binding.id != binding_id:
                continue
            manifest = self._registry.find(binding.adapter_id)
            if manifest is None or manifest.kind is not kind:
                continue
            if not manifest.supports(capability):
                continue
            return binding
        if binding_id is not None:
            raise OnboardingValidationError(
                f"connector {binding_id} is not an enabled {kind.value} connector "
                f"for {app_id} that supports {capability!r}"
            )
        return None


def _surviving_override(previous: MappedAction, fresh: MappedAction) -> Optional[RiskLevel]:
    """An operator's risk override survives a re-import unless the application
    has since declared a *higher* risk than the override raised it to - in which
    case the manifest wins and the override is dropped rather than lowering it."""
    if previous.risk_override is None:
        return None
    ranks = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3}
    if ranks[previous.risk_override] < ranks[fresh.manifest_risk]:
        return None
    return previous.risk_override


def _surviving_mode(previous: MappedAction, fresh: MappedAction) -> ExecutionMode:
    """A re-import can only make an action *less* automatic. If the application
    reclassified an action into a high-risk class, an existing AUTO setting is
    dropped to approval rather than carried into a class that may never
    auto-execute."""
    if fresh.high_risk and previous.execution_mode is ExecutionMode.AUTO:
        return ExecutionMode.APPROVAL
    return previous.execution_mode
