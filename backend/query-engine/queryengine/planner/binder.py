"""Semantic analysis: resolve names against the catalog and coerce literals.

The parser accepts any identifier and leaves literals as written. Binding is the
pass that checks those names exist and rewrites each literal into the Python
value its column actually stores, so that ``WHERE id = '101'`` compares integers
and not an integer against a string.
"""

from __future__ import annotations

from ..catalog import TableSchema
from ..errors import CatalogError
from ..sql.ast import (
    Between,
    ColumnRef,
    Compare,
    Expression,
    IsNull,
    Literal,
    Logical,
    Not,
)


def bind_expression(expression: Expression | None, schema: TableSchema) -> Expression | None:
    """Return the expression with names validated and literals coerced."""
    if expression is None:
        return None
    if isinstance(expression, Logical):
        return Logical(
            expression.operator,
            bind_expression(expression.left, schema),
            bind_expression(expression.right, schema),
        )
    if isinstance(expression, Not):
        return Not(bind_expression(expression.operand, schema))
    if isinstance(expression, Compare):
        return _bind_compare(expression, schema)
    if isinstance(expression, Between):
        column = _require_column(expression.operand, schema)
        return Between(
            ColumnRef(column.name),
            _coerce(expression.lower, column, schema),
            _coerce(expression.upper, column, schema),
            expression.negated,
        )
    if isinstance(expression, IsNull):
        column = _require_column(expression.operand, schema)
        return IsNull(ColumnRef(column.name), expression.negated)
    if isinstance(expression, ColumnRef):
        return ColumnRef(schema.column(expression.name).name)
    return expression


def bind_projection(projection: tuple[str, ...] | None, schema: TableSchema) -> tuple[str, ...]:
    """Resolve a projection list to canonical column names."""
    if projection is None:
        return schema.column_names
    return tuple(schema.column(name).name for name in projection)


def _bind_compare(expression: Compare, schema: TableSchema) -> Compare:
    left, right = expression.left, expression.right
    if isinstance(left, ColumnRef) and isinstance(right, Literal):
        column = schema.column(left.name)
        return Compare(ColumnRef(column.name), expression.operator, _coerce(right, column, schema))
    if isinstance(left, Literal) and isinstance(right, ColumnRef):
        column = schema.column(right.name)
        return Compare(_coerce(left, column, schema), expression.operator, ColumnRef(column.name))
    if isinstance(left, ColumnRef) and isinstance(right, ColumnRef):
        return Compare(
            ColumnRef(schema.column(left.name).name),
            expression.operator,
            ColumnRef(schema.column(right.name).name),
        )
    raise CatalogError("una comparacion debe involucrar al menos una columna")


def _require_column(operand: Expression, schema: TableSchema):
    if not isinstance(operand, ColumnRef):
        raise CatalogError("se esperaba una columna a la izquierda del predicado")
    return schema.column(operand.name)


def _coerce(operand: Expression, column, schema: TableSchema) -> Expression:
    if not isinstance(operand, Literal):
        return operand
    if operand.value is None:
        return operand
    return Literal(column.type.coerce(operand.value))
