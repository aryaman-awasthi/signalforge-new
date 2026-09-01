"""Blast radius, and the roles it requires in the room (REQ-045).

Membership is derived, not typed. An incident commander opening a war room at
03:00 should not also have to decide who ought to be in it, and a room whose
membership was a free-text list would be a room whose composition depended on
who happened to be awake.

So the room is assembled in two steps, and they are kept apart deliberately:

1. **What is affected** - `BlastRadius`, computed from the declaration itself:
   the severity, the regions and journeys detection named, whether a customer is
   feeling it, and the services the topology says sit downstream of the
   application. Facts about the incident, with no notion of people.
2. **Which roles that requires** - `required_roles`, a pure function of the
   radius. Facts about roles, with no notion of who holds them.

Resolving roles to people is the application layer's job, because it needs role
bindings and rotations. Keeping the three apart is what makes the middle step
testable as a table, which is what it is.

**A role with nobody in it is a gap, and a gap is stated.** If a P0 requires a
comms lead and no user holds that role for this application, the room says so on
its face rather than quietly rendering a shorter list. The failure mode this
avoids is the expensive one: nobody notices the customer was never told until
the post-incident review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from signalforge.contracts.canonical import TopologySnapshot
from signalforge.contracts.roles import Role
from signalforge.domain.onboarding.profile import Severity

MAX_SERVICES = 50
"""How many downstream services a radius names. A topology with a thousand nodes
is a real thing; a war-room header listing a thousand services is not."""


@dataclass(frozen=True)
class BlastRadius:
    """What this incident reaches.

    Every field is something measured or declared, never inferred. `services` is
    the set of topology nodes reachable *from* the application's own nodes: what
    depends on the thing that is broken, which is the direction that matters when
    deciding who needs to be in the room. `truncated` says the graph had more.
    """

    app_id: str
    severity: Severity
    regions: tuple[str, ...] = ()
    journeys: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    customer_facing: bool = False
    journey_tier: Optional[int] = None
    regions_total: int = 0
    truncated: bool = False
    source: str = "proposal"
    """Where the radius came from: `proposal` when detection had a verdict at
    declare time, `declaration` when it did not and only the severity a person
    typed was available. A room assembled without a verdict is still a room, and
    the field says which kind it is rather than pretending they are the same."""

    @property
    def multi_region(self) -> bool:
        return len(self.regions) > 1

    @property
    def whole_estate(self) -> bool:
        """Every region the application declares is affected. Not the same as
        `multi_region`: two of two is total, two of six is not."""
        return bool(self.regions) and self.regions_total > 0 and len(
            self.regions
        ) >= self.regions_total

    def summary(self) -> str:
        parts = [f"{self.severity.value} on {self.app_id}"]
        if self.regions:
            scope = "all regions" if self.whole_estate else ", ".join(self.regions)
            parts.append(scope)
        if self.journeys:
            parts.append(f"journeys {', '.join(self.journeys)}")
        if self.services:
            shown = ", ".join(self.services[:5])
            more = f" (+{len(self.services) - 5} more)" if len(self.services) > 5 else ""
            parts.append(f"downstream: {shown}{more}")
        if self.customer_facing:
            parts.append("customer-facing")
        return "; ".join(parts)


@dataclass(frozen=True)
class RoleRequirement:
    """One role the radius calls for, and the sentence that says why.

    The reason is carried rather than recomputed for display, because it is
    evidence: it goes on the member row, into the audit entry and onto the page
    itself, so somebody woken at 03:00 is told why *they* were.
    """

    role: Role
    reason: str
    paged: bool = True
    """Whether a holder is paged or merely added to the room. An executive is in
    the room for a P0 and is not woken by a pager for one; being present and
    being paged are different things, and conflating them is how executives stop
    reading their notifications."""


IC_REASON = "every incident has one commander; a room without one is a crowd"
TECH_LEAD_REASON = "the application's technical owner"


def required_roles(radius: BlastRadius) -> tuple[RoleRequirement, ...]:
    """The roles this blast radius calls into the room.

    A total function over the radius, in declaration order, and every rule below
    is a rule somebody has to be able to argue with:

    | Role | When | Paged |
    |---|---|---|
    | incident commander | always | yes |
    | tech lead | always | yes |
    | SME | anything downstream, any journey, or P0/P1 | yes |
    | comms lead | a customer is feeling it | yes |
    | executive | P0, or P1 across the whole estate | no - present, not woken |

    Two roles are deliberately never required. The **auditor** is read-only by
    the frozen matrix and can change nothing during an incident; paging one is
    noise, and they can join any room they can read. The **org owner** is an
    administrative role, not an operational one - if the owner is also the person
    who should be in the room, they hold an operational role as well, and the
    binding says so.
    """
    requirements: list[RoleRequirement] = [
        RoleRequirement(Role.INCIDENT_COMMANDER, IC_REASON),
        RoleRequirement(Role.TECH_LEAD, TECH_LEAD_REASON),
    ]

    sme_reason = _sme_reason(radius)
    if sme_reason:
        requirements.append(RoleRequirement(Role.SME, sme_reason))

    if radius.customer_facing:
        requirements.append(
            RoleRequirement(
                Role.COMMS_LEAD,
                "a customer is feeling this, so somebody owns what they are told",
            )
        )

    if radius.severity is Severity.P0:
        requirements.append(
            RoleRequirement(
                Role.EXECUTIVE,
                "P0: leadership is informed, and is present rather than paged",
                paged=False,
            )
        )
    elif radius.severity is Severity.P1 and radius.whole_estate:
        requirements.append(
            RoleRequirement(
                Role.EXECUTIVE,
                "P1 affecting every region: leadership is informed",
                paged=False,
            )
        )

    return tuple(requirements)


def _sme_reason(radius: BlastRadius) -> str:
    if radius.services:
        shown = ", ".join(radius.services[:3])
        return f"the people who know {shown}"
    if radius.journeys:
        return f"the journeys detection named: {', '.join(radius.journeys[:3])}"
    if radius.severity in (Severity.P0, Severity.P1):
        return (
            f"{radius.severity.value} with no topology to narrow it: whoever is "
            "on call for this application"
        )
    return ""


def downstream_services(
    snapshot: Optional[TopologySnapshot],
    *,
    app_id: str,
    regions: Sequence[str] = (),
    max_services: int = MAX_SERVICES,
) -> tuple[tuple[str, ...], bool]:
    """What depends on this application, walked from its own nodes outward.

    Edge direction is read as "source depends on target", which is how the
    canonical topology model declares it, so *dependents* are found by walking
    edges backwards: from a broken node to the things that named it. That is the
    direction blast radius means. Walking forwards would produce the
    application's own dependencies, which is a different and equally interesting
    question, and answering it here by accident is exactly the sort of quiet
    wrongness a war room cannot afford.

    Returns the service names and whether the walk was cut short. A truncated
    walk is reported rather than silently trimmed: an under-stated blast radius
    looks the same as a small one.
    """
    if snapshot is None or not snapshot.nodes:
        return (), False

    names: dict[str, str] = {node.node_id: node.name or node.node_id for node in snapshot.nodes}
    node_regions: dict[str, str] = {node.node_id: node.region or "" for node in snapshot.nodes}

    dependents: dict[str, set[str]] = {}
    for edge in snapshot.edges:
        dependents.setdefault(edge.target, set()).add(edge.source)

    seeds = _seed_nodes(snapshot, app_id=app_id, regions=regions)
    if not seeds:
        return (), False

    seen: set[str] = set(seeds)
    frontier: list[str] = list(seeds)
    reached: set[str] = set()
    while frontier:
        node_id = frontier.pop()
        for dependent in sorted(dependents.get(node_id, ())):
            if dependent in seen:
                continue
            seen.add(dependent)
            reached.add(dependent)
            frontier.append(dependent)

    # Region filtering is applied to the *result* rather than to the walk: a
    # dependency crossing regions is exactly the case a blast radius exists to
    # surface, so a path is never cut because its middle hop is elsewhere.
    if regions:
        wanted = {region for region in regions if region}
        reached = {
            node_id
            for node_id in reached
            if not node_regions.get(node_id) or node_regions[node_id] in wanted
        }

    ordered = sorted(names.get(node_id, node_id) for node_id in reached)
    return tuple(ordered[:max_services]), len(ordered) > max_services


def _seed_nodes(
    snapshot: TopologySnapshot, *, app_id: str, regions: Sequence[str]
) -> set[str]:
    """The application's own nodes: where the walk starts.

    Matched on the node id, its name, or an `app` label, because a topology
    document is written by whoever owns the estate and this platform does not get
    to dictate how they name things. With no match at all the walk finds nothing
    and says so, which is better than seeding from every node and calling the
    whole estate a blast radius.
    """
    wanted = {region for region in regions if region}
    seeds: set[str] = set()
    for node in snapshot.nodes:
        labels: Mapping[str, str] = node.labels or {}
        matches = (
            node.node_id == app_id
            or (node.name or "") == app_id
            or labels.get("app") == app_id
            or labels.get("app_id") == app_id
            or node.node_id.startswith(f"{app_id}-")
            or node.node_id.startswith(f"{app_id}:")
        )
        if not matches:
            continue
        if wanted and node.region and node.region not in wanted:
            continue
        seeds.add(node.node_id)
    return seeds


@dataclass(frozen=True)
class MembershipGap:
    """A required role nobody holds for this application.

    Rendered on the room and published on the timeline when the room opens. It
    is not an error - a war room with no comms lead still runs - but it is a fact
    somebody has to be able to see while they can still do something about it.
    """

    role: Role
    reason: str
    detail: str = ""
    fields: Mapping[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"no {self.role.value} is bound to this application: {self.reason}"
