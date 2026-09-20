"""Turning a WHERE clause into something the planner can price.

A predicate is *sargable* (search-argument-able) when it compares an indexed
column against a constant, because only then can an access path narrow the
search instead of filtering after the fact. This module pulls those out of the
expression tree and folds several of them on the same column into one range.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..sql.ast import (
    Between,
    ColumnRef,
    Compare,
    Comparison,
    Expression,
    Literal,
    Logical,
    LogicalOp,
    Not,
)


@dataclass(frozen=True)
class SargablePredicate:
    column: str
    operator: Comparison
    value: object


@dataclass
class KeyRange:
    """The window on one column implied by every predicate that mentions it."""

    column: str
    lower: object = None
    upper: object = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True
    equality: object = None
    has_equality: bool = False
    empty: bool = False

    @property
    def is_equality(self) -> bool:
        return self.has_equality and not self.empty

    @property
    def is_bounded(self) -> bool:
        return self.lower is not None or self.upper is not None

    def tighten(self, predicate: SargablePredicate) -> None:
        operator, value = predicate.operator, predicate.value
        if operator is Comparison.EQ:
            if self.has_equality and self.equality != value:
                self.empty = True
            self.has_equality = True
            self.equality = value
            self.lower = self.upper = value
            self.lower_inclusive = self.upper_inclusive = True
            return
        if operator in (Comparison.GT, Comparison.GE):
            inclusive = operator is Comparison.GE
            if self.lower is None or value > self.lower or (value == self.lower and not inclusive):
                self.lower = value
                self.lower_inclusive = inclusive
        elif operator in (Comparison.LT, Comparison.LE):
            inclusive = operator is Comparison.LE
            if self.upper is None or value < self.upper or (value == self.upper and not inclusive):
                self.upper = value
                self.upper_inclusive = inclusive
        self._check_empty()

    def _check_empty(self) -> None:
        if self.lower is None or self.upper is None:
            return
        try:
            if self.lower > self.upper:
                self.empty = True
            elif self.lower == self.upper:
                self.empty = not (self.lower_inclusive and self.upper_inclusive)
        except TypeError:
            self.empty = False

    def describe(self) -> str:
        if self.empty:
            return f"{self.column} sin valores posibles"
        if self.is_equality:
            return f"{self.column} = {self.equality!r}"
        left = "-inf" if self.lower is None else repr(self.lower)
        right = "+inf" if self.upper is None else repr(self.upper)
        open_bracket = "[" if self.lower_inclusive else "("
        close_bracket = "]" if self.upper_inclusive else ")"
        return f"{self.column} en {open_bracket}{left}, {right}{close_bracket}"


def split_conjuncts(expression: Expression | None) -> list[Expression]:
    """Flatten the top level AND chain; anything else stays whole.

    Only top level conjunctions are split: each one must hold for a row to
    qualify, so any of them may drive an access path. A disjunction cannot,
    which is why OR is returned untouched and ends up as a filter.
    """
    if expression is None:
        return []
    if isinstance(expression, Logical) and expression.operator is LogicalOp.AND:
        return split_conjuncts(expression.left) + split_conjuncts(expression.right)
    if isinstance(expression, Between) and not expression.negated and isinstance(
        expression.operand, ColumnRef
    ):
        return [
            Compare(expression.operand, Comparison.GE, expression.lower),
            Compare(expression.operand, Comparison.LE, expression.upper),
        ]
    if isinstance(expression, Not):
        inverted = _invert(expression.operand)
        if inverted is not None:
            return split_conjuncts(inverted)
    return [expression]


def as_sargable(expression: Expression) -> SargablePredicate | None:
    """Read a conjunct as ``column OP literal``, normalising the operand order."""
    if not isinstance(expression, Compare):
        return None
    left, right, operator = expression.left, expression.right, expression.operator
    if isinstance(left, ColumnRef) and isinstance(right, Literal):
        column, value = left.name, right.value
    elif isinstance(left, Literal) and isinstance(right, ColumnRef):
        column, value, operator = right.name, left.value, operator.flipped()
    else:
        return None
    if value is None or operator is Comparison.NE:
        return None  # NULL and <> never narrow a search
    return SargablePredicate(column, operator, value)


def key_ranges(conjuncts: list[Expression]) -> dict[str, KeyRange]:
    """Fold every sargable conjunct into one range per column."""
    ranges: dict[str, KeyRange] = {}
    for conjunct in conjuncts:
        predicate = as_sargable(conjunct)
        if predicate is None:
            continue
        key = predicate.column.lower()
        ranges.setdefault(key, KeyRange(predicate.column)).tighten(predicate)
    return ranges


def _invert(expression: Expression) -> Expression | None:
    """Push a NOT through a comparison so it stays sargable."""
    if isinstance(expression, Compare):
        opposite = {
            Comparison.EQ: Comparison.NE,
            Comparison.NE: Comparison.EQ,
            Comparison.LT: Comparison.GE,
            Comparison.LE: Comparison.GT,
            Comparison.GT: Comparison.LE,
            Comparison.GE: Comparison.LT,
        }[expression.operator]
        return Compare(expression.left, opposite, expression.right)
    return None
