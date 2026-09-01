"""Governance ports (CLAUDE.md rule 1).

Tenant isolation is enforced inside the implementations exactly as everywhere
else: every method takes `tenant_id` and the predicate lives in the repository.
There is one cross-tenant method in this file - `ExecutionLedgerRepository
.due_for_reconciliation` - and it exists because an execution left UNCERTAIN by a
process that died is a platform-wide work list that a background sweep has to be
able to find. It returns records, is called only by the worker, and never by a
request path; the same shape as the event dispatcher's sweep and for the same
reason.

`find_by_key` carries the duplicate-prevention rule and is a repository method
rather than a service loop for the reason `IncidentGraphRepository.upsert` is:
"look for an existing execution and create one only if there is none" has to be
one transaction, or two concurrent submits both find nothing and both execute.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Sequence

from signalforge.domain.governance.approvals import Approval
from signalforge.domain.governance.comms import Audience, Communication
from signalforge.domain.governance.ledger import ExecutionRecord, LedgerState
from signalforge.domain.governance.nba import ResponsePlan
from signalforge.domain.governance.policy import PolicyDocument
from signalforge.domain.governance.validation import ValidationCycle


class PolicyCodec(ABC):
    """Text in, `PolicyDocument` out.

    A port rather than a function because the *format* is infrastructure - this
    deployment writes YAML, another could accept JSON or a signed bundle - while
    the floor the parsed document must satisfy is domain. Keeping them apart is
    what stops a second implementation of the floor appearing inside a parser.
    """

    @abstractmethod
    def parse(
        self,
        text: str,
        *,
        tenant_id: str,
        app_id: str,
        version: int,
        at: datetime,
        author_id: str,
        author_label: str = "",
        note: str = "",
    ) -> PolicyDocument:
        """Raises `PolicyFloorViolation` for an unsafe document and
        `GovernanceValidationError` for a malformed one. The caller audits the
        first and simply reports the second."""

    @abstractmethod
    def dump(self, policy: PolicyDocument) -> str:
        """The document as an operator would write it, for seeding the editor."""


class PolicyRepository(ABC):
    """Versioned policy documents. Append-only: editing writes a new version and
    the old one stays readable, because an approval granted under version 3 is
    unreviewable if version 3 no longer exists."""

    @abstractmethod
    async def add(self, document: PolicyDocument) -> PolicyDocument:
        """Insert one version. Unique on (tenant, app, version)."""

    @abstractmethod
    async def latest(self, tenant_id: str, app_id: str) -> Optional[PolicyDocument]: ...

    @abstractmethod
    async def get(
        self, tenant_id: str, app_id: str, version: int
    ) -> Optional[PolicyDocument]: ...

    @abstractmethod
    async def history(
        self, tenant_id: str, app_id: str, *, limit: int = 50
    ) -> Sequence[PolicyDocument]:
        """Newest first. The editor's version list and an auditor's trail."""


class ExecutionLedgerRepository(ABC):
    @abstractmethod
    async def add(self, record: ExecutionRecord) -> ExecutionRecord: ...

    @abstractmethod
    async def update(self, record: ExecutionRecord) -> ExecutionRecord:
        """Persist a state move. The row is updated in place and every move also
        becomes an event, so the row is the current state and the event stream is
        the history."""

    @abstractmethod
    async def get(self, tenant_id: str, execution_id: str) -> Optional[ExecutionRecord]: ...

    @abstractmethod
    async def find_by_key(
        self, tenant_id: str, idempotency_key: str
    ) -> Optional[ExecutionRecord]:
        """The duplicate-prevention lookup. Unique on (tenant, key)."""

    @abstractmethod
    async def list(
        self,
        tenant_id: str,
        incident_id: str,
        *,
        states: Optional[Sequence[LedgerState]] = None,
        limit: int = 200,
    ) -> Sequence[ExecutionRecord]: ...

    @abstractmethod
    async def open_for_action(
        self, tenant_id: str, incident_id: str, action_id: str
    ) -> Sequence[ExecutionRecord]:
        """Executions of this action that have not reached a terminal state.

        What "is this already running?" reads, and what stops a second commander
        starting the same failover while the first one is still in flight.
        """

    @abstractmethod
    async def due_for_reconciliation(
        self, *, older_than: datetime, limit: int = 50
    ) -> Sequence[ExecutionRecord]:
        """Platform-wide: UNCERTAIN rows nobody has reconciled. Worker only."""


class ApprovalRepository(ABC):
    @abstractmethod
    async def add(self, approval: Approval) -> Approval: ...

    @abstractmethod
    async def update(self, approval: Approval) -> Approval: ...

    @abstractmethod
    async def get(self, tenant_id: str, approval_id: str) -> Optional[Approval]: ...

    @abstractmethod
    async def for_execution(
        self, tenant_id: str, execution_id: str
    ) -> Sequence[Approval]:
        """Every approval ever raised against one execution, newest first."""

    @abstractmethod
    async def list(
        self, tenant_id: str, incident_id: str, *, limit: int = 100
    ) -> Sequence[Approval]: ...

    @abstractmethod
    async def expire_due(self, *, now: datetime, limit: int = 100) -> Sequence[Approval]:
        """Platform-wide: mark granted approvals whose expiry has passed.

        Housekeeping, not enforcement. `Approval.assert_valid_now` refuses an
        expired approval whether or not this ever runs; this is what makes the
        board show `expired` instead of a countdown at -00:42.
        """


class ResponsePlanRepository(ABC):
    @abstractmethod
    async def add(self, plan: ResponsePlan) -> ResponsePlan: ...

    @abstractmethod
    async def latest(
        self, tenant_id: str, incident_id: str
    ) -> Optional[ResponsePlan]: ...

    @abstractmethod
    async def list(
        self, tenant_id: str, incident_id: str, *, limit: int = 20
    ) -> Sequence[ResponsePlan]:
        """Newest first. The plan-diff triad reads the top two."""


class CommunicationRepository(ABC):
    @abstractmethod
    async def add(self, communication: Communication) -> Communication: ...

    @abstractmethod
    async def update(self, communication: Communication) -> Communication: ...

    @abstractmethod
    async def get(
        self, tenant_id: str, communication_id: str
    ) -> Optional[Communication]: ...

    @abstractmethod
    async def list(
        self,
        tenant_id: str,
        incident_id: str,
        *,
        audience: Optional[Audience] = None,
        limit: int = 100,
    ) -> Sequence[Communication]: ...

    @abstractmethod
    async def issued_grounded_on(
        self, tenant_id: str, incident_id: str, node_ids: Sequence[str]
    ) -> Sequence[Communication]:
        """Issued communications citing any of these nodes.

        The contradiction sweep's only query: when a node is superseded, this is
        how the platform finds the statements that just stopped being true.
        """


class ValidationRepository(ABC):
    @abstractmethod
    async def add(self, cycle: ValidationCycle) -> ValidationCycle: ...

    @abstractmethod
    async def update(self, cycle: ValidationCycle) -> ValidationCycle: ...

    @abstractmethod
    async def get(
        self, tenant_id: str, validation_id: str
    ) -> Optional[ValidationCycle]: ...

    @abstractmethod
    async def latest(
        self, tenant_id: str, incident_id: str
    ) -> Optional[ValidationCycle]:
        """The cycle the closure gate reads."""

    @abstractmethod
    async def list(
        self, tenant_id: str, incident_id: str, *, limit: int = 50
    ) -> Sequence[ValidationCycle]: ...

    @abstractmethod
    async def failed_cycles(self, tenant_id: str, incident_id: str) -> int:
        """How many times this incident has claimed recovery and been refused.
        The escalation rule's only input."""
