"""In-memory storage used for development and tests.

These are stand-ins for the real disk layer, not competing implementations of
it. They exist so the parser, planner and executor can be built and tested
before the paged Heap File, Sequential File, B+ tree and dynamic hash are
finished, and so the test suite runs without touching the filesystem.

They model block transfers rather than performing them: costs are derived from
the schema's records-per-page, so the telemetry pipeline is exercised end to end
and the figures have the right shape. They are not measurements of real I/O --
point the engine at the real adapters before running any benchmark.

``MemoryTableStore`` satisfies StorageEngine, ``MemoryIndexStore`` satisfies
IndexManager, and both share one IOCounter so their costs add up.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable, Iterator

from ..catalog import IndexKind, IndexMeta, StorageKind, TableSchema
from ..errors import StorageUnavailableError
from .port import RID, IOCounter, Record, ReorganizeReport

FILL_FACTOR = 0.75
LEAF_FANOUT = 64


class _Table:
    """One table: its records plus the primary-key order for SEQUENTIAL tables."""

    def __init__(self, schema: TableSchema):
        self.schema = schema
        self.records: dict[RID, Record] = {}
        self.order: list[RID] = []
        self.keys: list = []
        self.overflow: set[RID] = set()
        self._next_slot = 0
        primary = schema.primary_key
        self._key_position = schema.index_of(primary.name) if primary else None

    @property
    def per_page(self) -> int:
        return self.schema.records_per_page

    @property
    def is_ordered(self) -> bool:
        return self.schema.storage is StorageKind.SEQUENTIAL

    def allocate(self) -> RID:
        rid = (self._next_slot // self.per_page, self._next_slot % self.per_page)
        self._next_slot += 1
        return rid

    def page_count(self) -> int:
        return max(1, math.ceil(len(self.records) / self.per_page))

    def key_of(self, record: Record):
        if self._key_position is None:
            return None
        return record[self._key_position]

    def place(self, rid: RID, record: Record) -> None:
        self.records[rid] = record
        if not self.is_ordered:
            self.order.append(rid)
            return
        key = self.key_of(record)
        position = bisect.bisect_right(self.keys, key)
        self.keys.insert(position, key)
        self.order.insert(position, rid)
        if position < len(self.order) - 1:
            self.overflow.add(rid)

    def remove(self, rid: RID) -> bool:
        if rid not in self.records:
            return False
        position = self.order.index(rid)
        self.order.pop(position)
        if self.is_ordered:
            self.keys.pop(position)
        self.overflow.discard(rid)
        del self.records[rid]
        return True

    def slice_by_key(self, lower, upper) -> Iterator[tuple[RID, Record]]:
        start = bisect.bisect_left(self.keys, lower) if lower is not None else 0
        end = bisect.bisect_right(self.keys, upper) if upper is not None else len(self.keys)
        for position in range(start, end):
            rid = self.order[position]
            yield rid, self.records[rid]


class MemoryTableStore:
    """Heap and Sequential table access, backed by dictionaries."""

    def __init__(self, io: IOCounter | None = None):
        self.io = io or IOCounter()
        self._tables: dict[str, _Table] = {}

    def create_table(self, schema: TableSchema) -> None:
        self._tables[schema.name.lower()] = _Table(schema)
        self.io.write()

    def drop_table(self, table: str) -> None:
        self._tables.pop(table.lower(), None)

    def insert(self, table: str, record: Record) -> RID:
        entry = self._table(table)
        rid = entry.allocate()
        if entry.is_ordered:
            self.io.read(self._binary_search_cost(entry))
        else:
            self.io.read()  # locate a page with free space through the free list
        entry.place(rid, tuple(record))
        self.io.write()
        return rid

    def fetch(self, table: str, rid: RID) -> Record | None:
        record = self._table(table).records.get(rid)
        if record is not None:
            self.io.read()
        return record

    def scan(self, table: str) -> Iterator[tuple[RID, Record]]:
        entry = self._table(table)
        for position, rid in enumerate(list(entry.order)):
            if position % entry.per_page == 0:
                self.io.read()
            record = entry.records.get(rid)
            if record is not None:
                yield rid, record

    def delete(self, table: str, rid: RID) -> bool:
        entry = self._table(table)
        self.io.read()
        if not entry.remove(rid):
            return False
        self.io.write()
        return True

    def page_count(self, table: str) -> int:
        return self._table(table).page_count()

    def search_key(self, table: str, key) -> list[tuple[RID, Record]]:
        entry = self._require_ordered(table)
        self.io.read(self._binary_search_cost(entry))
        return list(entry.slice_by_key(key, key))

    def range_key(self, table: str, lower, upper) -> Iterator[tuple[RID, Record]]:
        entry = self._require_ordered(table)
        self.io.read(self._binary_search_cost(entry))
        for position, (rid, record) in enumerate(entry.slice_by_key(lower, upper)):
            if position % entry.per_page == 0:
                self.io.read()
            yield rid, record

    def reorganize(self, table: str) -> ReorganizeReport:
        entry = self._require_ordered(table)
        pages_before = entry.page_count()
        from_overflow = len(entry.overflow)
        paired = sorted(
            ((entry.key_of(record), rid, record) for rid, record in entry.records.items()),
            key=lambda item: item[0],
        )
        entry.keys = [key for key, _, _ in paired]
        entry.order = [rid for _, rid, _ in paired]
        entry.overflow.clear()
        self.io.read(pages_before)
        per_page = max(1, int(entry.per_page * FILL_FACTOR))
        pages_after = max(1, math.ceil(len(entry.records) / per_page))
        self.io.write(pages_after)
        return ReorganizeReport(
            records_kept=len(entry.records),
            records_from_overflow=from_overflow,
            pages_before=pages_before,
            pages_after=pages_after,
            fill_factor=FILL_FACTOR,
        )

    def _table(self, table: str) -> _Table:
        try:
            return self._tables[table.lower()]
        except KeyError:
            message = f"la tabla '{table}' no esta abierta en storage"
            raise StorageUnavailableError(message) from None

    def _require_ordered(self, table: str) -> _Table:
        entry = self._table(table)
        if not entry.is_ordered:
            raise StorageUnavailableError(
                f"'{entry.schema.name}' usa {entry.schema.storage.value} "
                "y no ofrece acceso ordenado"
            )
        return entry

    @staticmethod
    def _binary_search_cost(entry: _Table) -> int:
        return max(1, math.ceil(math.log2(entry.page_count() + 1)))


class MemoryIndexStore:
    """B+ tree and hash index access, backed by a sorted key list."""

    def __init__(self, io: IOCounter | None = None):
        self.io = io or IOCounter()
        self._indexes: dict[str, dict] = {}

    def create_index(self, meta: IndexMeta, schema: TableSchema) -> None:
        self._indexes[meta.name.lower()] = {
            "table": meta.table.lower(),
            "kind": meta.kind,
            "entries": {},
            "keys": [],
        }
        self.io.write()

    def drop_index(self, name: str) -> None:
        self._indexes.pop(name.lower(), None)

    def insert(self, name: str, key, rid: RID) -> None:
        index = self._index(name)
        bucket = index["entries"].setdefault(key, [])
        if not bucket:
            bisect.insort(index["keys"], key)
        bucket.append(rid)
        self.io.write()

    def delete(self, name: str, key, rid: RID) -> None:
        index = self._index(name)
        bucket = index["entries"].get(key)
        if not bucket:
            return
        if rid in bucket:
            bucket.remove(rid)
        if not bucket:
            del index["entries"][key]
            position = bisect.bisect_left(index["keys"], key)
            if position < len(index["keys"]) and index["keys"][position] == key:
                index["keys"].pop(position)
        self.io.write()

    def search(self, name: str, key) -> list[RID]:
        index = self._index(name)
        self.io.read(self.height(name))
        return list(index["entries"].get(key, ()))

    def range_search(self, name: str, lower, upper) -> list[RID]:
        index = self._index(name)
        if index["kind"] is not IndexKind.BTREE:
            raise StorageUnavailableError(
                f"el indice '{name}' es {index['kind'].value} y no admite busqueda por rango"
            )
        keys = index["keys"]
        start = bisect.bisect_left(keys, lower) if lower is not None else 0
        end = bisect.bisect_right(keys, upper) if upper is not None else len(keys)
        self.io.read(self.height(name))
        found: list[RID] = []
        for position in range(start, end):
            if (position - start) % LEAF_FANOUT == 0:
                self.io.read()
            found.extend(index["entries"][keys[position]])
        return found

    def height(self, name: str) -> int:
        index = self._index(name)
        if index["kind"] is IndexKind.HASH:
            return 1
        return max(1, math.ceil(math.log(max(2, len(index["keys"])), LEAF_FANOUT)))

    def bulk_load(self, name: str, entries: Iterable[tuple[object, RID]]) -> None:
        for key, rid in entries:
            self.insert(name, key, rid)

    def _index(self, name: str) -> dict:
        try:
            return self._indexes[name.lower()]
        except KeyError:
            message = f"el indice '{name}' no esta abierto en storage"
            raise StorageUnavailableError(message) from None


def memory_backend() -> tuple[MemoryTableStore, MemoryIndexStore, IOCounter]:
    """Build a table store and an index store that share one counter."""
    io = IOCounter()
    return MemoryTableStore(io), MemoryIndexStore(io), io
