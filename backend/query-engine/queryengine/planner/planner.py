"""Access path selection.

The planner turns a bound statement into a physical plan. Its only real job is
the one the assignment names: look at the WHERE clause, enumerate the access
paths the catalog makes available, price each one, and keep the cheapest.

    equality + HASH or BTREE index        -> IndexScan
    range    + BTREE index                -> IndexRangeScan
    primary key on a SEQUENTIAL table     -> SequentialSearch / SequentialRangeScan
    anything else                         -> SeqScan

Whatever the chosen path does not enforce is re-checked by a Filter above it, so
the plan is correct regardless of which path wins.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..catalog import Catalog, IndexKind, IndexMeta, StorageKind, TableSchema
from ..errors import PlannerError
from ..sql import ast
from ..storage.port import IndexManager
from . import cost
from .binder import bind_expression, bind_projection
from .plan import (
    DDLPlan,
    DeletePlan,
    Filter,
    IndexRangeScan,
    IndexScan,
    InsertPlan,
    Limit,
    PlanNode,
    Project,
    SeqScan,
    SequentialRangeScan,
    SequentialSearch,
    Sort,
)
from .predicates import KeyRange, as_sargable, key_ranges, split_conjuncts


@dataclass
class _Candidate:
    node: PlanNode
    consumed: list[ast.Expression]


class Planner:
    def __init__(self, catalog: Catalog, indexes: IndexManager | None = None):
        self._catalog = catalog
        self._indexes = indexes

    def plan(self, statement: ast.Statement) -> PlanNode:
        if isinstance(statement, ast.Select):
            return self._plan_select(statement)
        if isinstance(statement, ast.Delete):
            return self._plan_delete(statement)
        if isinstance(statement, ast.Insert):
            return self._plan_insert(statement)
        if isinstance(statement, ast.CreateTable):
            return DDLPlan(
                operation="CreateTable",
                target=statement.table,
                attributes={"storage": statement.storage, "columns": len(statement.columns)},
            )
        if isinstance(statement, ast.DropTable):
            return DDLPlan(operation="DropTable", target=statement.table)
        if isinstance(statement, ast.CreateIndex):
            return DDLPlan(
                operation="CreateIndex",
                target=statement.name,
                attributes={
                    "on": f"{statement.table}({statement.column})",
                    "using": statement.kind,
                },
            )
        if isinstance(statement, ast.DropIndex):
            return DDLPlan(operation="DropIndex", target=statement.name)
        raise PlannerError(f"no hay plan para {type(statement).__name__}")

    # -- statements -----------------------------------------------------

    def _plan_select(self, statement: ast.Select) -> PlanNode:
        schema = self._catalog.table(statement.table)
        projection = bind_projection(statement.projection, schema)
        where = bind_expression(statement.where, schema)

        node = self._access_path(schema, where)
        if statement.order_by is not None:
            column = schema.column(statement.order_by.column).name
            node = Sort(child=node, column=column, descending=statement.order_by.descending)
        if statement.limit is not None:
            node = Limit(child=node, count=statement.limit)
        if projection != schema.column_names:
            node = Project(child=node, columns=projection)
        return node

    def _plan_delete(self, statement: ast.Delete) -> PlanNode:
        schema = self._catalog.table(statement.table)
        where = bind_expression(statement.where, schema)
        touched = tuple(meta.name for meta in self._catalog.indexes_on(schema.name))
        return DeletePlan(
            table=schema.name,
            child=self._access_path(schema, where),
            index_updates=touched,
        )

    def _plan_insert(self, statement: ast.Insert) -> PlanNode:
        schema = self._catalog.table(statement.table)
        touched = tuple(meta.name for meta in self._catalog.indexes_on(schema.name))
        return InsertPlan(
            table=schema.name,
            row_count=len(statement.rows),
            index_updates=touched,
        )

    # -- access path selection ------------------------------------------

    def _access_path(self, schema: TableSchema, where: ast.Expression | None) -> PlanNode:
        statistics = self._catalog.statistics(schema.name)
        conjuncts = split_conjuncts(where)
        ranges = key_ranges(conjuncts)

        candidates = [self._full_scan(schema, statistics)]
        for key_range in ranges.values():
            if key_range.empty:
                continue
            candidates.extend(self._indexed_paths(schema, statistics, key_range, conjuncts))
            candidates.extend(self._ordered_paths(schema, statistics, key_range, conjuncts))

        best = min(candidates, key=lambda candidate: candidate.node.estimate.blocks)
        residual = [conjunct for conjunct in conjuncts if conjunct not in best.consumed]
        if not residual:
            return best.node
        predicate = _rebuild_conjunction(residual)
        return Filter(
            child=best.node,
            predicate=predicate,
            text=_render(predicate),
            estimate=best.node.estimate,
        )

    def _full_scan(self, schema: TableSchema, statistics) -> _Candidate:
        estimate = cost.seq_scan(schema, statistics)
        return _Candidate(SeqScan(table=schema.name, estimate=estimate), consumed=[])

    def _indexed_paths(
        self,
        schema: TableSchema,
        statistics,
        key_range: KeyRange,
        conjuncts: list[ast.Expression],
    ) -> list[_Candidate]:
        found: list[_Candidate] = []
        rows = cost.expected_rows(key_range, statistics)
        consumed = _conjuncts_for(conjuncts, key_range.column)
        for meta in self._catalog.indexes_on(schema.name, key_range.column):
            height = self._height(meta)
            if key_range.is_equality:
                estimate = cost.index_equality(height, rows, meta.kind is IndexKind.HASH)
                found.append(
                    _Candidate(
                        IndexScan(
                            table=schema.name,
                            index=meta.name,
                            kind=meta.kind,
                            column=key_range.column,
                            key=key_range.equality,
                            estimate=estimate,
                        ),
                        consumed,
                    )
                )
            elif key_range.is_bounded and meta.kind.supports_range:
                estimate = cost.index_range(height, rows)
                found.append(
                    _Candidate(
                        IndexRangeScan(
                            table=schema.name,
                            index=meta.name,
                            column=key_range.column,
                            lower=key_range.lower,
                            upper=key_range.upper,
                            lower_inclusive=key_range.lower_inclusive,
                            upper_inclusive=key_range.upper_inclusive,
                            estimate=estimate,
                        ),
                        consumed,
                    )
                )
        return found

    def _ordered_paths(
        self,
        schema: TableSchema,
        statistics,
        key_range: KeyRange,
        conjuncts: list[ast.Expression],
    ) -> list[_Candidate]:
        primary = schema.primary_key
        if schema.storage is not StorageKind.SEQUENTIAL or primary is None:
            return []
        if primary.name.lower() != key_range.column.lower():
            return []
        rows = cost.expected_rows(key_range, statistics)
        consumed = _conjuncts_for(conjuncts, key_range.column)
        if key_range.is_equality:
            estimate = cost.binary_search(schema, statistics, rows)
            return [
                _Candidate(
                    SequentialSearch(
                        table=schema.name,
                        column=primary.name,
                        key=key_range.equality,
                        estimate=estimate,
                    ),
                    consumed,
                )
            ]
        if key_range.is_bounded:
            estimate = cost.sequential_range(schema, statistics, rows)
            return [
                _Candidate(
                    SequentialRangeScan(
                        table=schema.name,
                        column=primary.name,
                        lower=key_range.lower,
                        upper=key_range.upper,
                        estimate=estimate,
                    ),
                    consumed,
                )
            ]
        return []

    def _height(self, meta: IndexMeta) -> int:
        if self._indexes is None:
            return 3  # textbook default for a tree that has not reported yet
        try:
            return max(1, self._indexes.height(meta.name))
        except Exception:
            return 3


def _conjuncts_for(conjuncts: list[ast.Expression], column: str) -> list[ast.Expression]:
    """The conjuncts an access path on ``column`` fully enforces."""
    target = column.lower()
    matched = []
    for conjunct in conjuncts:
        predicate = as_sargable(conjunct)
        if predicate is not None and predicate.column.lower() == target:
            matched.append(conjunct)
    return matched


def _rebuild_conjunction(conjuncts: list[ast.Expression]) -> ast.Expression:
    node = conjuncts[0]
    for extra in conjuncts[1:]:
        node = ast.Logical(ast.LogicalOp.AND, node, extra)
    return node


def _render(expression: ast.Expression) -> str:
    """Readable form of a predicate, for the plan shown in the client."""
    if isinstance(expression, ast.Logical):
        left, right = _render(expression.left), _render(expression.right)
        return f"({left} {expression.operator.value} {right})"
    if isinstance(expression, ast.Not):
        return f"NOT {_render(expression.operand)}"
    if isinstance(expression, ast.Compare):
        return f"{_render(expression.left)} {expression.operator.value} {_render(expression.right)}"
    if isinstance(expression, ast.Between):
        negation = "NOT " if expression.negated else ""
        return (
            f"{_render(expression.operand)} {negation}BETWEEN "
            f"{_render(expression.lower)} AND {_render(expression.upper)}"
        )
    if isinstance(expression, ast.IsNull):
        return f"{_render(expression.operand)} IS {'NOT ' if expression.negated else ''}NULL"
    if isinstance(expression, ast.ColumnRef):
        return expression.name
    if isinstance(expression, ast.Literal):
        return repr(expression.value)
    return str(expression)
