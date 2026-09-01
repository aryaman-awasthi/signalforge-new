"""Checkpoint expressions: expected versus observed, and nothing else (REQ-070).

A checkpoint reads a value the drill has already captured from a real response
and compares it with a literal written in the scenario file. It cannot call
anything, cannot compute anything the product would otherwise compute, and
cannot express "roughly". That is deliberate: the moment a drill's assertion
language can do arithmetic, the drill starts re-implementing the thing it is
supposed to be checking, and a green run stops meaning the product is right.

Paths are dotted, with integer segments indexing lists and ``[]`` projecting a
list of dicts onto one field:

    run.execution.state
    board.layers[].verdict
    ledger.executions.0.state
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

MISSING = object()
"""Distinct from None, which is a value a payload may legitimately hold."""

OPERATORS: tuple[str, ...] = (
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "length",
    "min_length",
    "matches",
    "present",
    "absent",
    "each_equals",
    "each_in",
    "equals_path",
    "not_equals_path",
    "gt_path",
    "lt_path",
    "contains_path",
)

PATH_OPERATORS = frozenset(
    {"equals_path", "not_equals_path", "gt_path", "lt_path", "contains_path"}
)
"""Operators whose operand is another path rather than a literal.

"this hypothesis is more confident than it was before the measurement arrived"
is a claim about two real observations, and writing it as a literal threshold
would turn it into a claim about a number somebody typed. Both sides are still
values the product returned; the drill only says which is expected to be larger.
"""


@dataclass(frozen=True)
class Expectation:
    """One clause of a checkpoint: a path, an operator and its operand."""

    path: str
    operator: str
    operand: Any

    def describe(self) -> str:
        return f"{self.path} {self.operator} {self.operand!r}"


@dataclass(frozen=True)
class CheckResult:
    expectation: Expectation
    observed: Any
    ok: bool

    @property
    def detail(self) -> str:
        seen = "<missing>" if self.observed is MISSING else repr(self.observed)
        return f"{self.expectation.describe()} but observed {seen}"


def resolve(root: Any, path: str) -> Any:
    """Walk a dotted path, returning MISSING rather than raising.

    A checkpoint against a path the response does not have is a *failed*
    checkpoint with a legible message, not a KeyError three frames deep.
    """
    current: Any = root
    for segment in _segments(path):
        if current is MISSING:
            return MISSING
        if segment == "[]":
            if not isinstance(current, list):
                return MISSING
            current = _Projection(current)
            continue
        if isinstance(current, _Projection):
            current = _Projection(
                [_child(item, segment) for item in current.items]
            )
            continue
        current = _child(current, segment)
    if isinstance(current, _Projection):
        return list(current.items)
    return current


def evaluate(root: Any, expectation: Expectation) -> CheckResult:
    observed = resolve(root, expectation.path)
    if expectation.operator in PATH_OPERATORS:
        other = resolve(root, str(expectation.operand))
        if other is MISSING:
            return CheckResult(expectation=expectation, observed=observed, ok=False)
        operator = expectation.operator[: -len("_path")]
        return CheckResult(
            expectation=expectation,
            observed=observed,
            ok=_compare(observed, operator, other),
        )
    return CheckResult(
        expectation=expectation,
        observed=observed,
        ok=_compare(observed, expectation.operator, expectation.operand),
    )


class _Projection:
    """The intermediate value ``[]`` produces: a list being mapped over."""

    def __init__(self, items: list[Any]):
        self.items = items


def _segments(path: str) -> list[str]:
    out: list[str] = []
    for part in path.split("."):
        while part.endswith("[]"):
            part = part[:-2]
            if part:
                out.append(part)
                part = ""
            out.append("[]")
        if part:
            out.append(part)
    return out


def _child(node: Any, segment: str) -> Any:
    if isinstance(node, dict):
        return node.get(segment, MISSING)
    if isinstance(node, list):
        if segment.lstrip("-").isdigit():
            index = int(segment)
            if -len(node) <= index < len(node):
                return node[index]
        return MISSING
    return MISSING


def _compare(observed: Any, operator: str, operand: Any) -> bool:  # noqa: C901
    if operator == "present":
        return (observed is not MISSING) is bool(operand)
    if operator == "absent":
        return (observed is MISSING) is bool(operand)
    if observed is MISSING:
        return False
    if operator == "equals":
        return observed == operand
    if operator == "not_equals":
        return observed != operand
    if operator == "contains":
        return _containable(observed) and operand in observed
    if operator == "not_contains":
        return _containable(observed) and operand not in observed
    if operator == "in":
        return isinstance(operand, (list, tuple, str)) and observed in operand
    if operator == "not_in":
        return isinstance(operand, (list, tuple, str)) and observed not in operand
    if operator == "length":
        return _sized(observed) and len(observed) == operand
    if operator == "min_length":
        return _sized(observed) and len(observed) >= operand
    if operator == "matches":
        return isinstance(observed, str) and re.search(str(operand), observed) is not None
    if operator == "each_equals":
        return isinstance(observed, list) and all(item == operand for item in observed)
    if operator == "each_in":
        return isinstance(observed, list) and all(item in operand for item in observed)
    if operator in {"gt", "gte", "lt", "lte"}:
        return _ordered(observed, operator, operand)
    raise ValueError(f"unknown checkpoint operator {operator!r}")


def _containable(value: Any) -> bool:
    return isinstance(value, (list, tuple, str, dict, set))


def _sized(value: Any) -> bool:
    return isinstance(value, (list, tuple, str, dict, set))


def _ordered(observed: Any, operator: str, operand: Any) -> bool:
    if isinstance(observed, bool) or not isinstance(observed, (int, float)):
        return False
    if isinstance(operand, bool) or not isinstance(operand, (int, float)):
        return False
    if operator == "gt":
        return observed > operand
    if operator == "gte":
        return observed >= operand
    if operator == "lt":
        return observed < operand
    return observed <= operand


def parse_expectations(raw: Any, *, where: str) -> tuple[Expectation, ...]:
    """Read a scenario's ``expect:`` block.

    Two shapes are accepted because both read well in YAML: a list of clauses
    with an explicit operator, and a mapping of path to expected value, which
    means ``equals``.
    """
    if raw is None:
        return ()
    out: list[Expectation] = []
    if isinstance(raw, dict):
        for path, value in raw.items():
            out.append(Expectation(path=str(path), operator="equals", operand=value))
        return tuple(out)
    if not isinstance(raw, list):
        raise ValueError(f"{where}: expect must be a list or a mapping")
    for clause in raw:
        if not isinstance(clause, dict):
            raise ValueError(f"{where}: every expect clause must be a mapping")
        path = clause.get("path")
        if not path:
            raise ValueError(f"{where}: every expect clause needs a path")
        operator, operand = _operator_of(clause, where=where, path=str(path))
        out.append(Expectation(path=str(path), operator=operator, operand=operand))
    return tuple(out)


def _operator_of(clause: dict, *, where: str, path: str) -> tuple[str, Any]:
    found: Optional[str] = None
    for name in OPERATORS:
        if name in clause:
            if found is not None:
                raise ValueError(
                    f"{where}: clause {path} names two operators ({found}, {name})"
                )
            found = name
    if found is None:
        raise ValueError(
            f"{where}: clause {path} names no operator; one of {list(OPERATORS)}"
        )
    return found, clause[found]
