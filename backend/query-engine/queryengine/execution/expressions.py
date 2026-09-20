"""Evaluating a bound predicate against one row.

SQL truth is three valued: a comparison against NULL yields UNKNOWN, not false,
and only rows whose predicate evaluates to TRUE survive a WHERE. Python's None
stands for UNKNOWN here, which is why the logical operators are spelled out
instead of relying on ``and`` / ``or``.
"""

from __future__ import annotations

from ..errors import PlannerError
from ..sql.ast import (
    Between,
    ColumnRef,
    Compare,
    Comparison,
    Expression,
    IsNull,
    Literal,
    Logical,
    LogicalOp,
    Not,
)

_COMPARATORS = {
    Comparison.EQ: lambda a, b: a == b,
    Comparison.NE: lambda a, b: a != b,
    Comparison.LT: lambda a, b: a < b,
    Comparison.LE: lambda a, b: a <= b,
    Comparison.GT: lambda a, b: a > b,
    Comparison.GE: lambda a, b: a >= b,
}


def matches(expression: Expression | None, row: tuple, positions: dict[str, int]) -> bool:
    """True only when the predicate evaluates to TRUE, never on UNKNOWN."""
    if expression is None:
        return True
    return evaluate(expression, row, positions) is True


def evaluate(expression: Expression, row: tuple, positions: dict[str, int]):
    if isinstance(expression, Literal):
        return expression.value
    if isinstance(expression, ColumnRef):
        try:
            return row[positions[expression.name.lower()]]
        except KeyError:
            raise PlannerError(f"la columna '{expression.name}' no esta en la fila") from None
    if isinstance(expression, Compare):
        left = evaluate(expression.left, row, positions)
        right = evaluate(expression.right, row, positions)
        if left is None or right is None:
            return None
        try:
            return _COMPARATORS[expression.operator](left, right)
        except TypeError:
            return None
    if isinstance(expression, Logical):
        left = evaluate(expression.left, row, positions)
        right = evaluate(expression.right, row, positions)
        if expression.operator is LogicalOp.AND:
            return _and(left, right)
        return _or(left, right)
    if isinstance(expression, Not):
        value = evaluate(expression.operand, row, positions)
        return None if value is None else not value
    if isinstance(expression, Between):
        value = evaluate(expression.operand, row, positions)
        lower = evaluate(expression.lower, row, positions)
        upper = evaluate(expression.upper, row, positions)
        if value is None or lower is None or upper is None:
            return None
        try:
            inside = lower <= value <= upper
        except TypeError:
            return None
        return not inside if expression.negated else inside
    if isinstance(expression, IsNull):
        value = evaluate(expression.operand, row, positions)
        return (value is not None) if expression.negated else (value is None)
    raise PlannerError(f"no se puede evaluar {type(expression).__name__}")


def _and(left, right):
    if left is False or right is False:
        return False
    if left is None or right is None:
        return None
    return True


def _or(left, right):
    if left is True or right is True:
        return True
    if left is None or right is None:
        return None
    return False
