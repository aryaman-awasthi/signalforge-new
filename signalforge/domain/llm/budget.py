"""Per-incident token accounting (REQ-051).

Pure arithmetic with no clock and no I/O, so the rule that matters - an incident
cannot spend past its budget - is testable without a database or a gateway.

Two design points worth stating:

- **The budget is checked before the call and charged after it.** The check uses
  an estimate, because nobody knows the real cost until the answer comes back.
  An over-spend of one call is therefore possible and accepted; an *unbounded*
  over-spend is not, which is what the pre-check prevents.
- **Work with no incident shares one lane.** Onboarding a runbook or answering a
  question outside an incident is charged to `NO_INCIDENT_LANE`, so background
  work can never quietly consume an incident's budget - or hide from accounting.
"""

from __future__ import annotations

from typing import Optional

from signalforge.domain.llm.entities import TokenUsage
from signalforge.domain.llm.ports import TokenLedger

NO_INCIDENT_LANE = "_no-incident"
"""The lane everything outside an incident is charged to. Not a tenant id and
deliberately not spelled like one: budgets are per incident, and mixing the two
namespaces is how a background job ends up spending a P0's allowance."""

DEFAULT_INCIDENT_TOKEN_BUDGET = 200_000


def budget_key(incident_id: Optional[str]) -> str:
    return incident_id or NO_INCIDENT_LANE


class InMemoryTokenLedger(TokenLedger):
    """One process, one ledger. Right for the single-node deployment and behind
    the port, so a multi-node install swaps in a shared counter without a caller
    changing."""

    def __init__(self, limit: int = DEFAULT_INCIDENT_TOKEN_BUDGET):
        if limit <= 0:
            raise ValueError("a token budget must be positive")
        self._limit = limit
        self._spent: dict[str, TokenUsage] = {}

    @property
    def limit(self) -> int:
        return self._limit

    def spent(self, key: str) -> TokenUsage:
        return self._spent.get(key, TokenUsage())

    def remaining(self, key: str) -> int:
        return max(0, self._limit - self.spent(key).total)

    def would_exceed(self, key: str, estimate: int) -> bool:
        return self.spent(key).total + max(0, estimate) > self._limit

    def charge(self, key: str, usage: TokenUsage) -> None:
        self._spent[key] = self.spent(key) + usage

    def reset(self, key: str) -> None:
        self._spent.pop(key, None)


def estimate_tokens(text: str) -> int:
    """A deliberately crude pre-call estimate: ~4 characters per token.

    It is used only to decide whether a call may start, and it errs high on
    prose (the thing this platform actually sends), which is the safe direction
    for a spending guard. Real usage from the gateway replaces it afterwards.
    """
    return max(1, (len(text) + 3) // 4)
