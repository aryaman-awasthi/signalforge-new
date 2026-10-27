"""Next best action, and the plan it becomes (REQ-063).

The division of labour here is the point of the whole sprint, so it is worth
stating flatly:

- **The score is arithmetic**, computed in `domain/governance/nba.py` from the
  incident's severity, its blast radius, the hypothesis each action addresses and
  the action's own risk class. It is computed and stored before any model is
  asked anything, and a node with no model reachable produces exactly the same
  ranking.
- **The model writes one sentence.** `rationale` is prose *about* a score that
  already exists. It cannot change the ordering, cannot add a candidate, cannot
  remove one, and cannot make an ineligible action eligible - it is handed the
  numbers and asked to explain them in English.

Everything the ranking reads is a stored fact:

| Term | Read from |
|---|---|
| urgency | the incident's severity, which a person set, and whether the blast radius is customer-facing |
| impact | the action class's mitigation power, scaled by the confidence of the hypothesis it addresses |
| information gain | the action class, raised when the action discriminates between live hypotheses |
| risk | the policy engine's risk class, reduced for reversible and idempotent actions |

## Eligibility

Three gates, evaluated before ranking, and a candidate that fails any of them is
returned with `eligible=False` and a reason rather than hidden. It can never be
the primary recommendation.

- **Policy**: the engine returned BLOCK.
- **Permission**: nobody in this tenant holds a role that could approve it. An
  action nobody can authorise is not a plan.
- **Prerequisite**: the action's declared preconditions that the platform can
  actually check are not met - a rollback with no change to roll back to, an
  action with no actions connector bound.

Preconditions the platform cannot evaluate are listed as declared-but-unchecked
rather than assumed true or assumed false, because an application's own
precondition strings are its vocabulary, not ours.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from signalforge.application.events.bus import EventBus
from signalforge.application.governance.common import (
    NBA_COMPUTED_TOPIC,
    PLAN_REVISED_TOPIC,
    publish_system,
    record,
)
from signalforge.application.governance.policy_service import PolicyService
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.roles import ROLE_PERMISSIONS
from signalforge.domain.brain.nodes import GraphNode, NodeType
from signalforge.domain.brain.untrusted import SYSTEM_FLOOR, UntrustedItem, fence
from signalforge.domain.governance.nba import (
    ACT_CONFIDENCE_THRESHOLD,
    ActionCandidate,
    Eligibility,
    Recommendation,
    ResponsePlan,
    Score,
    expected_validation_s,
    impact_of,
    information_gain_of,
    is_action_causally_supported,
    recommend,
    revise_plan,
    risk_of,
    urgency_of,
)
from signalforge.domain.governance.policy import (
    Outcome,
    PolicyDocument,
    PolicyEngine,
    classify_action,
)
from signalforge.domain.identity.ports import Clock
from signalforge.domain.incidents.errors import IncidentNotFoundError
from signalforge.domain.llm.entities import CompletionRequest, Message, ModelClass
from signalforge.domain.llm.ports import LlmProvider
from signalforge.domain.onboarding.profile import MappedAction, OnboardingProfile

logger = logging.getLogger("signalforge.governance")

PURPOSE = "nba-rationale"

CHECKABLE_PRECONDITIONS: frozenset[str] = frozenset(
    {"change_window_open", "incident_active", "topology_known", "actions_connector"}
)
"""The precondition vocabulary this platform understands. Anything else an
application declares is shown to the operator and not evaluated - inventing a
verdict for a string we cannot check would be worse than admitting we cannot."""

RATIONALE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rationales"],
    "properties": {
        "rationales": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action_id", "text"],
                "properties": {
                    "action_id": {"type": "string", "maxLength": 100},
                    "text": {"type": "string", "maxLength": 400},
                },
            },
        }
    },
}


@dataclass(frozen=True)
class NbaView:
    recommendation: Recommendation
    plan: Optional[ResponsePlan]
    previous_plan: Optional[ResponsePlan]
    policy: PolicyDocument


class NextBestActionService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        bus: EventBus,
        clock: Clock,
        policies: PolicyService,
        llm: LlmProvider,
        registry,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._policies = policies
        self._llm = llm
        self._registry = registry
        self._metrics = metrics

    # -- reads ---------------------------------------------------------------

    async def recommend(
        self, principal: Principal, incident_id: str, *, explain: bool = True
    ) -> NbaView:
        """Rank the actions and, if a model is reachable, have it explain them.

        `explain=False` is what the plan revision and the tests use: the ranking
        with no model call at all, which is also what a degraded node produces.
        """
        candidates, policy, act_threshold = await self._candidates(
            principal, incident_id
        )
        if explain and candidates:
            candidates = await self._explain(principal, incident_id, candidates)
        result = recommend(
            candidates, at=self._clock.now(), act_threshold=act_threshold
        )
        async with self._uow_factory() as uow:
            plan = await uow.response_plans.latest(principal.tenant_id, incident_id)
            history = list(
                await uow.response_plans.list(principal.tenant_id, incident_id, limit=2)
            )
        previous = history[1] if len(history) > 1 else None
        return NbaView(
            recommendation=result, plan=plan, previous_plan=previous, policy=policy
        )

    async def plans(
        self, principal: Principal, incident_id: str, *, limit: int = 10
    ) -> Sequence[ResponsePlan]:
        async with self._uow_factory() as uow:
            await self._require_incident(uow, principal.tenant_id, incident_id)
            return list(
                await uow.response_plans.list(
                    principal.tenant_id, incident_id, limit=limit
                )
            )

    # -- plan revision -------------------------------------------------------

    async def revise(
        self,
        principal: Principal,
        incident_id: str,
        *,
        changed_assumptions: Sequence[str] = (),
        note: str = "",
    ) -> NbaView:
        """Write plan version n+1, with every action marked against version n.

        Never a replacement. The previous version stays, the diff is stored, and
        the assumption that changed travels with it - because "we stopped doing
        that at 03:14 because the pool metric came back clean" is the sentence a
        post-incident review is made of.
        """
        now = self._clock.now()
        candidates, policy, act_threshold = await self._candidates(
            principal, incident_id
        )
        async with self._uow_factory() as uow:
            incident = await self._require_incident(
                uow, principal.tenant_id, incident_id
            )
            previous = await uow.response_plans.latest(
                principal.tenant_id, incident_id
            )
            evidence = await self._evidence_refs(uow, principal.tenant_id, incident_id)
            plan = revise_plan(
                previous,
                candidates,
                plan_id=str(uuid.uuid4()),
                tenant_id=principal.tenant_id,
                incident_id=incident_id,
                at=now,
                by=principal.user_id,
                changed_assumptions=changed_assumptions,
                evidence_refs=evidence,
                note=note,
            )
            await uow.response_plans.add(plan)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=PLAN_REVISED_TOPIC,
                payload=plan.as_dict(),
            )
            await uow.commit()
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_response_plans_total",
                {"kept": str(len(plan.kept)), "paused": str(len(plan.paused))},
            )
        return NbaView(
            recommendation=recommend(candidates, at=now, act_threshold=act_threshold),
            plan=plan,
            previous_plan=previous,
            policy=policy,
        )

    # -- internals -----------------------------------------------------------

    async def _candidates(
        self, principal: Principal, incident_id: str
    ) -> tuple[tuple[ActionCandidate, ...], PolicyDocument, float]:
        """The candidates, the policy in force, and the confidence the
        application's onboarding policy asks for before a remediation outranks
        a diagnostic (`confidence_threshold`, which until S10 was stored and
        read by nothing)."""
        async with self._uow_factory() as uow:
            incident = await self._require_incident(
                uow, principal.tenant_id, incident_id
            )
            profile = await uow.onboarding.get(principal.tenant_id, incident.app_id)
            nodes = list(
                await uow.incident_graph.list(
                    principal.tenant_id,
                    incident_id,
                    types=[NodeType.HYPOTHESIS],
                    limit=50,
                )
            )
            evidence_nodes = list(
                await uow.incident_graph.list(
                    principal.tenant_id,
                    incident_id,
                    types=[NodeType.EVIDENCE, NodeType.FACT],
                    limit=40,
                )
            )
            changes = await uow.changes.list(
                principal.tenant_id, incident.app_id, limit=20
            )
            approver_roles = await self._roles_in_tenant(
                uow, principal.tenant_id, incident.app_id
            )
            has_actions_connector = await self._has_actions_connector(
                uow, principal.tenant_id, incident.app_id
            )
        policy = await self._policies.current(principal.tenant_id, incident.app_id)

        top = _top_hypothesis(nodes)
        # The RCA the brain wrote, read rather than re-derived: the action ranked
        # now has to be justifiable against the belief that was actually held
        # when it was ranked.
        root_cause, mechanism = _root_cause_of(top)
        evidence_text = _evidence_labels(top, evidence_nodes)
        customer_facing = _customer_facing(incident)
        urgency = urgency_of(
            incident.severity.value, customer_facing=customer_facing
        )
        context = _Context(
            change_window_open=bool(changes),
            incident_active=incident.state.open,
            topology_known=bool(profile.topology) if profile else False,
            actions_connector=has_actions_connector,
        )

        candidates: list[ActionCandidate] = []
        for action in _actions_of(profile):
            candidates.append(
                self._candidate(
                    action,
                    policy=policy,
                    urgency=urgency,
                    customer_facing=customer_facing,
                    top=top,
                    context=context,
                    approver_roles=approver_roles,
                    discriminating=_discriminates(action, nodes),
                    root_cause=root_cause,
                    mechanism=mechanism,
                    evidence_text=evidence_text,
                )
            )
        act_threshold = (
            float(profile.policy.confidence_threshold)
            if profile is not None and profile.policy is not None
            else ACT_CONFIDENCE_THRESHOLD
        )
        return tuple(candidates), policy, act_threshold

    def _candidate(
        self,
        action: MappedAction,
        *,
        policy: PolicyDocument,
        urgency: float,
        customer_facing: bool,
        top: Optional[GraphNode],
        context: "_Context",
        approver_roles: frozenset[str],
        discriminating: bool,
        root_cause: str = "",
        mechanism: str = "",
        evidence_text: Sequence[str] = (),
    ) -> ActionCandidate:
        decision = PolicyEngine.decide(action, policy)
        risk_class = classify_action(action)
        confidence = float(top.confidence or 0.0) if top is not None else 0.0
        # Before the action is ranked: how does it remove the mechanism? An
        # action that cannot answer that from the evidence is scored as what it
        # is and cannot be the primary recommendation while one that can exists.
        fit = is_action_causally_supported(
            root_cause, mechanism, action, evidence_text
        )
        score = Score(
            urgency=urgency,
            impact=impact_of(
                action.action_class,
                hypothesis_confidence=confidence,
                customer_facing=customer_facing,
                causal_fit=fit.factor,
            ),
            information_gain=information_gain_of(
                action.action_class, discriminating=discriminating
            ),
            risk=risk_of(
                risk_class,
                reversible=action.reversible,
                idempotent=action.idempotent,
            ),
        )
        unmet = context.unmet(action)
        eligibility, reason = _eligibility(
            decision, unmet=unmet, approver_roles=approver_roles
        )
        return ActionCandidate(
            action_id=action.action_id,
            name=action.name,
            action_class=action.action_class,
            risk_class=risk_class,
            score=score,
            decision=decision,
            eligibility=eligibility,
            eligibility_reason=reason,
            hypothesis_key=top.derived_key if top is not None else "",
            hypothesis_label=top.label if top is not None else "",
            hypothesis_confidence=confidence,
            evidence_refs=(top.id,) if top is not None else (),
            prerequisites=action.preconditions,
            unmet_prerequisites=unmet,
            expected_validation_s=expected_validation_s(
                len(action.verification),
                stability_window_s=policy.stability_window_s,
            ),
            rollback_action_id=action.reverse_action_id,
            rollback_summary=(
                f"reversible: {action.reverse_action_id} undoes it"
                if action.reversible and action.reverse_action_id
                else "no declared reverse action; compensation would be manual"
            ),
            reversible=action.reversible,
            idempotent=action.idempotent,
            params_schema=action.params_schema,
            root_cause=root_cause,
            causal_mechanism=mechanism,
            causally_supported=fit.supported,
            causal_fit=fit.factor,
            causal_reason=fit.reason,
        )

    async def _explain(
        self,
        principal: Principal,
        incident_id: str,
        candidates: Sequence[ActionCandidate],
    ) -> tuple[ActionCandidate, ...]:
        """One model call for the whole board, or the deterministic sentences.

        The deterministic rationale is written first and is what every candidate
        carries unless the model produced something usable for it. There is no
        path in which a candidate ends up with no explanation.
        """
        base = tuple(
            candidate.with_rationale(candidate.explain(), degraded=True)
            for candidate in candidates
        )
        top = [c for c in base if c.eligible][:4]
        if not top:
            return base
        result = await self._llm.complete(
            CompletionRequest(
                purpose=PURPOSE,
                messages=(
                    Message(role="system", content=SYSTEM_FLOOR),
                    Message(role="user", content=_prompt(top)),
                ),
                model_class=ModelClass.GENERAL,
                schema=RATIONALE_SCHEMA,
                fallback=None,
                tenant_id=principal.tenant_id,
                incident_id=incident_id,
                max_tokens=700,
            )
        )
        if result.degraded or not isinstance(result.data, dict):
            return base
        written = {
            str(item.get("action_id")): str(item.get("text") or "").strip()
            for item in (result.data.get("rationales") or [])
            if isinstance(item, dict)
        }
        out: list[ActionCandidate] = []
        for candidate in base:
            text = written.get(candidate.action_id, "")
            if text and len(text) <= 400:
                out.append(candidate.with_rationale(text, degraded=False))
            else:
                out.append(candidate)
        return tuple(out)

    async def announce(
        self, principal: Principal, incident_id: str, view: NbaView
    ) -> None:
        """Put the current recommendation on the timeline.

        A system event rather than a human one: nobody decided anything by
        looking at a ranking, and recording it as a human act would put a
        decision in the record that nobody made.
        """
        primary = view.recommendation.primary
        if primary is None:
            return
        async with self._uow_factory() as uow:
            incident = await self._require_incident(
                uow, principal.tenant_id, incident_id
            )
            await publish_system(
                uow,
                self._bus,
                self._clock,
                tenant_id=principal.tenant_id,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=NBA_COMPUTED_TOPIC,
                payload={
                    "incident_id": incident_id,
                    "primary": primary.as_dict(),
                    "alternatives": [
                        c.action_id for c in view.recommendation.alternatives
                    ],
                    "fallback": (
                        view.recommendation.fallback.action_id
                        if view.recommendation.fallback
                        else None
                    ),
                    "ineligible": [
                        {"action_id": c.action_id, "reason": c.eligibility_reason}
                        for c in view.recommendation.ineligible
                    ],
                    "degraded": view.recommendation.degraded,
                    "policy_version": view.policy.version,
                },
            )
            await uow.commit()

    @staticmethod
    async def _require_incident(
        uow: IdentityUnitOfWork, tenant_id: str, incident_id: str
    ):
        incident = await uow.incidents.get(tenant_id, incident_id)
        if incident is None:
            raise IncidentNotFoundError(f"incident {incident_id} not found")
        return incident

    @staticmethod
    async def _roles_in_tenant(
        uow: IdentityUnitOfWork, tenant_id: str, app_id: str
    ) -> frozenset[str]:
        """Which roles somebody actually holds for this application.

        Used only to answer "could anybody approve this?". A recommendation whose
        approver role nobody holds is a recommendation nobody can act on, and the
        war room should be told that before it spends ten minutes on it.
        """
        bindings = await uow.bindings.list_for_tenant(tenant_id)
        return frozenset(
            binding.role.value
            for binding in bindings
            if binding.app_id is None or binding.app_id == app_id
        )

    async def _has_actions_connector(
        self, uow: IdentityUnitOfWork, tenant_id: str, app_id: str
    ) -> bool:
        bindings = await uow.connectors.list(
            tenant_id, app_id=app_id, enabled_only=True
        )
        for binding in bindings:
            manifest = self._registry.find(binding.adapter_id)
            if manifest is not None and manifest.kind is AdapterKind.ACTIONS:
                return True
        return False

    @staticmethod
    async def _evidence_refs(
        uow: IdentityUnitOfWork, tenant_id: str, incident_id: str
    ) -> tuple[str, ...]:
        nodes = await uow.incident_graph.list(
            tenant_id, incident_id, types=[NodeType.EVIDENCE], limit=20
        )
        return tuple(node.id for node in nodes)


@dataclass(frozen=True)
class _Context:
    """The preconditions the platform can actually evaluate."""

    change_window_open: bool
    incident_active: bool
    topology_known: bool
    actions_connector: bool

    def unmet(self, action: MappedAction) -> tuple[str, ...]:
        held = {
            "change_window_open": self.change_window_open,
            "incident_active": self.incident_active,
            "topology_known": self.topology_known,
            "actions_connector": self.actions_connector,
        }
        declared = [
            name
            for name in action.preconditions
            if name in CHECKABLE_PRECONDITIONS and not held[name]
        ]
        if not self.actions_connector:
            # Universal: nothing executes without a bound actions connector, so
            # every candidate carries it whether or not the app declared it.
            declared.append("actions_connector")
        return tuple(dict.fromkeys(declared))


def _eligibility(
    decision, *, unmet: Sequence[str], approver_roles: frozenset[str]
) -> tuple[Eligibility, str]:
    if decision.outcome is Outcome.BLOCK:
        return Eligibility.BLOCKED_BY_POLICY, decision.reason
    if unmet:
        return (
            Eligibility.MISSING_PREREQUISITE,
            "prerequisites not met: " + ", ".join(unmet),
        )
    if decision.needs_approval:
        eligible = {role.value for role in decision.approver_roles}
        if eligible and not (eligible & approver_roles):
            return (
                Eligibility.NO_APPROVER,
                "nobody on this application holds "
                + " or ".join(sorted(eligible))
                + ", so nobody could approve it",
            )
    return Eligibility.ELIGIBLE, ""


def _actions_of(profile: Optional[OnboardingProfile]) -> tuple[MappedAction, ...]:
    if profile is None:
        return ()
    return tuple(action for action in profile.actions if action.enabled)


def _top_hypothesis(nodes: Sequence[GraphNode]) -> Optional[GraphNode]:
    """The leading hypothesis, or None. Read-only: the NBA scores against what
    the brain believes and never writes back to it."""
    live = [n for n in nodes if n.current and n.confidence is not None]
    if not live:
        return None
    return max(live, key=lambda n: (n.confidence or 0.0, n.seq))


def _root_cause_of(top: Optional[GraphNode]) -> tuple[str, str]:
    """The leading hypothesis's root cause and causal mechanism, as the brain
    recorded them.

    Empty when the brain has not reached a conclusion, and empty is the neutral
    answer: the causal check makes no judgement about an action when there is no
    established cause to judge it against.
    """
    if top is None:
        return "", ""
    detail = dict(top.detail or {})
    return (
        str(detail.get("root_cause") or "").strip(),
        str(detail.get("causal_mechanism") or "").strip(),
    )


def _evidence_labels(
    top: Optional[GraphNode], evidence_nodes: Sequence[GraphNode]
) -> tuple[str, ...]:
    """What the causal check reads besides the mechanism.

    The leading hypothesis's own supporting evidence first - it is the evidence
    that actually argued for this cause - then the incident's other current
    evidence, so a deployment link recorded by one agent is visible to a check
    reading another agent's hypothesis.
    """
    labels: list[str] = []
    if top is not None:
        detail = dict(top.detail or {})
        labels.extend(str(item) for item in (detail.get("supporting_evidence") or []))
    labels.extend(node.label for node in evidence_nodes if node.current and node.label)
    return tuple(dict.fromkeys(labels))[:30]


def _discriminates(action: MappedAction, nodes: Sequence[GraphNode]) -> bool:
    """Whether doing this would tell two live hypotheses apart.

    Approximated deterministically: two or more hypotheses are live and close
    together, and the action is one whose outcome distinguishes a change-related
    cause from an infrastructure one. Approximate, stated as approximate, and
    never a model's judgement.
    """
    live = sorted(
        (n.confidence or 0.0) for n in nodes if n.current and n.confidence is not None
    )
    if len(live) < 2:
        return False
    close = abs(live[-1] - live[-2]) <= 0.2
    return close and action.action_class.value in {"rollback", "diagnostic"}


def _customer_facing(incident) -> bool:
    """Whether the detector said customers were feeling this when it was declared.

    Read from the stored proposal rather than recomputed: the urgency term must
    be reproducible from the row six months later, and a live recomputation would
    give a different answer every time the recommendation is refreshed.
    """
    proposal = getattr(incident, "proposal", None)
    if proposal is None:
        return False
    return bool(getattr(proposal, "customer_impacting", False))


def _prompt(candidates: Sequence[ActionCandidate]) -> str:
    """The model is handed the numbers and forbidden to produce new ones.

    The scores are already computed and already stored. Asking the model to
    restate a confidence or invent a ranking would put a second, unaudited number
    beside the first - the same rule the hypothesis narrative follows.
    """
    block = fence(
        tuple(
            UntrustedItem(
                ref=candidate.action_id,
                kind="action-candidate",
                text=f"{candidate.name}: {candidate.explain()}",
            )
            for candidate in candidates
        ),
        salt="nba",
    )
    return (
        "Write one short sentence per action explaining, to an incident "
        "commander, why the platform ranked it where it did. SignalForge has "
        "already computed each score deterministically.\n\n"
        "Every sentence must be causal, not symptomatic. Before writing one, "
        "read from the material below:\n"
        "1. which resource or component is failing;\n"
        "2. the causal mechanism by which it produces the symptom;\n"
        "3. the evidence recorded for that mechanism;\n"
        "4. how this action acts on that mechanism;\n"
        "5. whether a safer or more reversible action would act on it too.\n\n"
        "Rules:\n"
        "- Explain the action in terms of the mechanism it removes. Never "
        "justify an action by the symptom, by its availability, or by its being "
        "a familiar remedy.\n"
        "- Where the material says an action is not causally supported, say so "
        "plainly and say what evidence would change that. Never argue against "
        "the platform's causal verdict.\n"
        "- A deployment existing near the onset is not evidence that it caused "
        "the fault. Do not treat it as a reason to undo the change.\n"
        "- Use only the material below. Add no new facts.\n"
        "- Do not invent, restate or adjust any number. The scores are shown "
        "beside your text.\n"
        "- Do not recommend an action that is not listed.\n"
        "- Do not say an action is safe. Say what it would do and what it would "
        "tell us.\n"
        "- One sentence each, under forty words, no preamble.\n\n"
        "THE RANKED ACTIONS (ground truth, computed from stored facts):\n"
        f"{block}"
    )
