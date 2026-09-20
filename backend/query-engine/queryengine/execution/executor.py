"""Plan execution, iterator style.

Every operator is a generator yielding ``(rid, record)`` pairs and pulling from
its child on demand, so a LIMIT really does stop the scan underneath it instead
of materialising the whole table first. Only Sort has to buffer.

RIDs travel alongside the records because DELETE needs them to reach the storage
layer and the index entries.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..catalog import Catalog, TableSchema
from ..errors import PlannerError
from ..planner.plan import (
    Filter,
    IndexRangeScan,
    IndexScan,
    Limit,
    PlanNode,
    Project,
    SeqScan,
    SequentialRangeScan,
    SequentialSearch,
    Sort,
)
from ..storage.port import RID, IndexManager, Record, StorageEngine
from .expressions import matches

Tuple = tuple[RID, Record]


class Executor:
    def __init__(self, catalog: Catalog, storage: StorageEngine, indexes: IndexManager):
        self._catalog = catalog
        self._storage = storage
        self._indexes = indexes

    def execute(self, node: PlanNode, schema: TableSchema) -> Iterator[Tuple]:
        if isinstance(node, SeqScan):
            return self._storage.scan(node.table)
        if isinstance(node, IndexScan):
            return self._index_scan(node)
        if isinstance(node, IndexRangeScan):
            return self._index_range_scan(node, schema)
        if isinstance(node, SequentialSearch):
            return iter(self._storage.search_key(node.table, node.key))
        if isinstance(node, SequentialRangeScan):
            return self._storage.range_key(node.table, node.lower, node.upper)
        if isinstance(node, Filter):
            return self._filter(node, schema)
        if isinstance(node, Sort):
            return self._sort(node, schema)
        if isinstance(node, Limit):
            return self._limit(node, schema)
        if isinstance(node, Project):
            return self._project(node, schema)
        raise PlannerError(f"el ejecutor no soporta el nodo {node.label}")

    def output_columns(self, node: PlanNode, schema: TableSchema) -> tuple[str, ...]:
        """Column names the plan emits, following Project nodes down the tree."""
        if isinstance(node, Project):
            return node.columns
        for child in node.children:
            return self.output_columns(child, schema)
        return schema.column_names

    # -- access paths ---------------------------------------------------

    def _index_scan(self, node: IndexScan) -> Iterator[Tuple]:
        for rid in self._indexes.search(node.index, node.key):
            record = self._storage.fetch(node.table, rid)
            if record is not None:
                yield rid, record

    def _index_range_scan(self, node: IndexRangeScan, schema: TableSchema) -> Iterator[Tuple]:
        position = schema.index_of(node.column)
        for rid in self._indexes.range_search(node.index, node.lower, node.upper):
            record = self._storage.fetch(node.table, rid)
            if record is None:
                continue
            if self._within_bounds(record[position], node):
                yield rid, record

    @staticmethod
    def _within_bounds(value, node: IndexRangeScan) -> bool:
        if node.lower is not None:
            if node.lower_inclusive:
                if value < node.lower:
                    return False
            elif value <= node.lower:
                return False
        if node.upper is not None:
            if node.upper_inclusive:
                if value > node.upper:
                    return False
            elif value >= node.upper:
                return False
        return True

    # -- shaping --------------------------------------------------------

    def _filter(self, node: Filter, schema: TableSchema) -> Iterator[Tuple]:
        positions = _positions(schema)
        for rid, record in self.execute(node.child, schema):
            if matches(node.predicate, record, positions):
                yield rid, record

    def _sort(self, node: Sort, schema: TableSchema) -> Iterator[Tuple]:
        position = schema.index_of(node.column)
        buffered = list(self.execute(node.child, schema))
        buffered.sort(key=lambda item: _sort_key(item[1][position]), reverse=node.descending)
        return iter(buffered)

    def _limit(self, node: Limit, schema: TableSchema) -> Iterator[Tuple]:
        if node.count <= 0:
            return
        for emitted, item in enumerate(self.execute(node.child, schema), start=1):
            yield item
            if emitted >= node.count:
                return

    def _project(self, node: Project, schema: TableSchema) -> Iterator[Tuple]:
        positions = [schema.index_of(name) for name in node.columns]
        for rid, record in self.execute(node.child, schema):
            yield rid, tuple(record[index] for index in positions)


def _positions(schema: TableSchema) -> dict[str, int]:
    return {column.name.lower(): index for index, column in enumerate(schema.columns)}


def _sort_key(value):
    """NULLs sort last, matching the default of most engines."""
    return (value is None, value if value is not None else 0)
