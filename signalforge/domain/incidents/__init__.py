"""The incident lifecycle: six states, three of which only a person can enter."""

from signalforge.domain.incidents.entities import (
    ChangeWindowLink,
    DeclaredProposal,
    EvidenceLink,
    Incident,
    IncidentTransition,
    SeveritySource,
    TicketSync,
    TicketSyncState,
)
from signalforge.domain.incidents.lifecycle import (
    HUMAN_ONLY,
    STANCES,
    TRANSITIONS,
    LifecycleState,
    assert_severity_change,
    assert_transition,
    state_for_stance,
)

__all__ = [
    "ChangeWindowLink",
    "DeclaredProposal",
    "EvidenceLink",
    "HUMAN_ONLY",
    "Incident",
    "IncidentTransition",
    "LifecycleState",
    "STANCES",
    "SeveritySource",
    "TRANSITIONS",
    "TicketSync",
    "TicketSyncState",
    "assert_severity_change",
    "assert_transition",
    "state_for_stance",
]
