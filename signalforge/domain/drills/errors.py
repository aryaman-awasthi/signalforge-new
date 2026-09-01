"""Drill errors.

A malformed scenario is a *load* failure, not a run failure: a drill that
half-ran because step nine had a typo is worse than one that never started, so
everything a scenario says is validated before the first step executes.
"""

from __future__ import annotations


class DrillError(Exception):
    """Base for everything the drill engine raises."""


class ScenarioError(DrillError):
    """The scenario document is not a scenario."""


class StepError(DrillError):
    """A step could not run, or ran and did not do what it said."""

    def __init__(self, step_id: str, message: str):
        self.step_id = step_id
        super().__init__(f"step {step_id}: {message}")


class CheckpointFailed(DrillError):
    """A checkpoint's observed value is not its expected value."""

    def __init__(self, step_id: str, failures: tuple[str, ...]):
        self.step_id = step_id
        self.failures = failures
        detail = "; ".join(failures)
        super().__init__(f"checkpoint {step_id} failed: {detail}")
