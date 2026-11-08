"""Plan execution, iterator style.

Every operator is a generator yielding ``(rid, record)`` pairs and pulling from
its child on demand, so a LIMIT really does stop the scan underneath it instead
of materialising the whole table first. Only Sort has to buffer.

RIDs travel alongside the records because DELETE needs them to reach the storage
layer and the index entries.
"""

from __future__ import annotations

import math
import time
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
    RTreeKNNScan,
    RTreeRadiusScan,
    RTreeWindowScan,
    SeqScan,
    SequentialRangeScan,
    SequentialSearch,
    Sort,
)
from ..storage.port import RID, IndexManager, Record, StorageEngine
from ..spatial import parse_point
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
        if isinstance(node, RTreeRadiusScan):
            return self._spatial_radius(node, schema)
        if isinstance(node, RTreeKNNScan):
            return self._spatial_knn(node, schema)
        if isinstance(node, RTreeWindowScan):
            return self._spatial_window(node, schema)
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

    def _spatial_window(self, node: RTreeWindowScan, schema: TableSchema) -> Iterator[Tuple]:
        io = self._indexes.io
        index_before = (io.disk_reads, io.disk_writes)
        started = time.perf_counter()
        candidates = (self._indexes.spatial_window(node.index, node.window)
                      if node.index else None)
        index_ms = (time.perf_counter() - started) * 1000
        index_delta = (io.disk_reads - index_before[0], io.disk_writes - index_before[1])
        position = schema.index_of(node.column)
        table_before = (io.disk_reads, io.disk_writes)
        table_started = time.perf_counter()
        output, mbrs = [], []
        if candidates is None:
            xmin, ymin, xmax, ymax = node.window
            for rid, record in self._storage.scan(node.table):
                value = record[position]
                if value is None:
                    continue
                x, y = parse_point(value)
                if xmin <= x <= xmax and ymin <= y <= ymax:
                    output.append((rid, record))
                    if node.include_mbrs:
                        mbrs.append((x, y, x, y))
        else:
            for rid, mbr in candidates:
                record = self._storage.fetch(node.table, rid)
                if record is not None:
                    output.append((rid, record))
                    if node.include_mbrs:
                        mbrs.append(tuple(mbr))
        table_ms = (time.perf_counter() - table_started) * 1000
        table_delta = (io.disk_reads - table_before[0], io.disk_writes - table_before[1])
        node.mbrs = mbrs
        node.runtime = {"index_reads": index_delta[0], "index_writes": index_delta[1],
                        "table_reads": table_delta[0], "table_writes": table_delta[1],
                        "index_ms": round(index_ms, 4), "table_ms": round(table_ms, 4)}
        return iter(output)

    def _spatial_radius(self, node: RTreeRadiusScan, schema: TableSchema) -> Iterator[Tuple]:
        io = self._indexes.io
        index_reads, index_writes = io.disk_reads, io.disk_writes
        started = time.perf_counter()
        candidates = (self._indexes.spatial_radius(node.index, node.point, node.radius)
                      if node.index else None)
        index_ms = (time.perf_counter() - started) * 1000
        after_reads, after_writes = io.disk_reads, io.disk_writes
        index_delta = (after_reads - index_reads, after_writes - index_writes)
        position = schema.index_of(node.column)
        table_started = time.perf_counter()
        table_before = (io.disk_reads, io.disk_writes)
        if candidates is None:
            output = []
            mbrs = []
            for rid, record in self._storage.scan(node.table):
                value = record[position]
                if value is not None:
                    point = parse_point(value)
                    if math.dist(point, node.point) <= node.radius:
                        output.append((rid, record))
                        if node.include_mbrs:
                            mbrs.append((point[0], point[1], point[0], point[1]))
        else:
            output = []
            mbrs = []
            for _, rid, _mbr in candidates:
                record = self._storage.fetch(node.table, rid)
                if record is not None:
                    output.append((rid, record))
                    if node.include_mbrs:
                        mbrs.append(tuple(_mbr))
        table_ms = (time.perf_counter() - table_started) * 1000
        table_delta = (io.disk_reads - table_before[0], io.disk_writes - table_before[1])
        node.runtime = {"index_reads": index_delta[0], "index_writes": index_delta[1],
                        "table_reads": table_delta[0], "table_writes": table_delta[1],
                        "index_ms": round(index_ms, 4), "table_ms": round(table_ms, 4)}
        node.mbrs = mbrs
        return iter(output)

    def _spatial_knn(self, node: RTreeKNNScan, schema: TableSchema) -> Iterator[Tuple]:
        io = self._indexes.io
        index_before = (io.disk_reads, io.disk_writes)
        position = schema.index_of(node.column)
        table_reads = table_writes = 0
        table_ms = 0.0
        index_ms = 0.0
        if node.index:
            output, seen = [], set()
            limit = node.count
            total = self._indexes.entry_count(node.index)
            while True:
                started = time.perf_counter()
                neighbors = self._indexes.spatial_knn(node.index, node.point, limit)
                index_ms += (time.perf_counter() - started) * 1000
                before = (io.disk_reads, io.disk_writes)
                started = time.perf_counter()
                for _, rid, _mbr in neighbors:
                    if rid in seen:
                        continue
                    seen.add(rid)
                    record = self._storage.fetch(node.table, rid)
                    if record is not None:
                        output.append((rid, record))
                        if len(output) == node.count:
                            break
                table_ms += (time.perf_counter() - started) * 1000
                table_reads += io.disk_reads - before[0]
                table_writes += io.disk_writes - before[1]
                if len(output) >= node.count or limit >= total:
                    break
                limit = min(total, max(limit * 2, node.count + 1))
        else:
            before = (io.disk_reads, io.disk_writes)
            started = time.perf_counter()
            all_rows = []
            for rid, record in self._storage.scan(node.table):
                value = record[position]
                if value is not None:
                    coords = parse_point(value)
                    all_rows.append((math.dist(coords, node.point), rid, record))
            all_rows.sort(key=lambda item: item[0])
            output = [(rid, record) for _, rid, record in all_rows[:node.count]]
            table_ms = (time.perf_counter() - started) * 1000
            table_reads = io.disk_reads - before[0]
            table_writes = io.disk_writes - before[1]
        index_delta = (io.disk_reads - index_before[0] - table_reads,
                       io.disk_writes - index_before[1] - table_writes)
        node.runtime = {"index_reads": index_delta[0], "index_writes": index_delta[1],
                        "table_reads": table_reads, "table_writes": table_writes,
                        "index_ms": round(index_ms, 4), "table_ms": round(table_ms, 4)}
        return iter(output)

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
