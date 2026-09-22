"""Table access over the physical page layer.

This binds the storage module's primitives -- slotted pages of 4 KB, a
``DiskManager`` that moves whole blocks with ``seek``, and the ``DiskCounter``
that tallies them -- to the ``StorageEngine`` contract the query engine
consumes. It owns only the table level mapping the page layer leaves open:

* one binary file per table, so a RID's ``page_id`` indexes that table's file
* placement on insert (fill the last page, otherwise allocate a new one)
* the full table scan, page by page
* logical deletion, which zeroes a slot's length in place so RIDs stay valid

It does NOT reimplement the page layout, the block transfers or the counter:
those come from the storage module and any fix there lands here for free.

A SEQUENTIAL table adds a second file, the overflow area. The main file holds
records ordered by primary key so it can be searched with a binary descent over
pages; new records land in the overflow area, which is scanned linearly, and
``reorganize`` merges the two back into an ordered main file at a fill factor
that leaves room for the next batch. Overflow pages are addressed with negative
page ids so a RID still names exactly one record without a third field.
"""

from __future__ import annotations

import contextlib
import os
import struct
from collections.abc import Iterator

from ..catalog import StorageKind, TableSchema
from ..errors import StorageUnavailableError
from .blk01 import PhysicalLayer, load
from .codec import RecordCodec
from .port import RID, IOCounter, Record, ReorganizeReport


class _CounterBridge:
    """Feeds the storage module's DiskCounter tallies into the engine's IOCounter.

    The engine's counter is shared with the index layer, so this contributes
    what the physical counters advanced since the last look instead of assigning
    absolute values -- assigning them would erase every transfer the indexes had
    already recorded for the same query.
    """

    def __init__(self, io: IOCounter):
        self._io = io
        self._counters: list = []
        self._seen_reads = 0
        self._seen_writes = 0

    def attach(self, counter) -> None:
        self._counters.append(counter)
        self.sync()

    def sync(self) -> None:
        reads = sum(counter.disk_reads for counter in self._counters)
        writes = sum(counter.disk_writes for counter in self._counters)
        self._io.disk_reads += reads - self._seen_reads
        self._io.disk_writes += writes - self._seen_writes
        self._seen_reads, self._seen_writes = reads, writes

    def retire(self, counter) -> None:
        """Drop a table's counter while keeping the transfers it already reported."""
        self.sync()
        if counter in self._counters:
            self._counters.remove(counter)
            self._seen_reads -= counter.disk_reads
            self._seen_writes -= counter.disk_writes


FILL_FACTOR = 0.75


class _Table:
    def __init__(self, schema: TableSchema, manager, codec: RecordCodec, overflow=None):
        self.schema = schema
        self.manager = manager
        self.codec = codec
        self.overflow = overflow      # second DiskManager, only for SEQUENTIAL
        self.tail = None              # the page inserts are currently filling
        self.dirty = False
        self.tail_overflow = False    # whether that page lives in the overflow area
        self.key_position = (
            schema.index_of(schema.primary_key.name) if schema.primary_key else None
        )

    @property
    def ordered(self) -> bool:
        return self.schema.storage is StorageKind.SEQUENTIAL

    @property
    def insert_manager(self):
        """Where new records land: the overflow area when the table is ordered."""
        return self.overflow if self.ordered else self.manager

    def key_of(self, record: Record):
        return record[self.key_position] if self.key_position is not None else None


class DiskTableStore:
    """StorageEngine backed by real 4 KB blocks on disk."""

    def __init__(self, io: IOCounter, data_dir: str, layer: PhysicalLayer | None = None):
        self.io = io
        self._layer = layer or load()
        self._data_dir = os.path.abspath(data_dir)
        os.makedirs(self._data_dir, exist_ok=True)
        self._tables: dict[str, _Table] = {}
        self._bridge = _CounterBridge(io)

    @property
    def page_size(self) -> int:
        return self._layer.page_size

    @property
    def source(self) -> str:
        return self._layer.source

    # -- lifecycle ------------------------------------------------------

    def create_table(self, schema: TableSchema) -> None:
        key = schema.name.lower()
        if key in self._tables:
            return  # reopening a table the catalog already knows
        self._check_page_size(schema)
        codec = RecordCodec(schema)
        capacity = schema.page_size - self._layer.header_size
        if codec.size + self._layer.slot_size > capacity:
            raise StorageUnavailableError(
                f"un registro de '{schema.name}' ocupa {codec.size} bytes y no entra en una "
                f"pagina de {schema.page_size} bytes"
            )
        manager = self._new_manager(schema)
        self._bridge.attach(manager.counter)
        overflow = None
        if schema.storage is StorageKind.SEQUENTIAL:
            overflow = self._new_manager(schema, suffix=".ovf")
            self._bridge.attach(overflow.counter)
        self._tables[key] = _Table(schema, manager, codec, overflow)
        self._bridge.sync()

    def drop_table(self, table: str) -> None:
        entry = self._tables.pop(table.lower(), None)
        if entry is None:
            return
        entry.tail, entry.dirty = None, False
        self._bridge.retire(entry.manager.counter)
        with contextlib.suppress(FileNotFoundError):
            os.remove(self._path(entry.schema.name))
        if entry.overflow is not None:
            self._bridge.retire(entry.overflow.counter)
            with contextlib.suppress(FileNotFoundError):
                os.remove(self._path(entry.schema.name, ".ovf"))

    # -- records --------------------------------------------------------

    def insert(self, table: str, record: Record) -> RID:
        """Place a record on the tail page, keeping that page in memory.

        Writing the whole 4 KB block after every record would cost one write per
        record rather than one per page -- a hundredfold amplification that would
        swamp the insertion experiment. The tail page stays resident until it
        fills or ``flush`` is called.
        """
        entry = self._table(table)
        raw = entry.codec.pack(record)

        if entry.tail is None:
            self._open_tail(entry)

        slot = entry.tail.insert_record(raw)
        if slot is None:
            self.flush(table)
            self._new_tail(entry)
            slot = entry.tail.insert_record(raw)
            if slot is None:
                raise StorageUnavailableError(
                    f"un registro de '{entry.schema.name}' no cabe en una pagina vacia"
                )
        entry.dirty = True
        page_id = entry.tail.page_id
        return (_overflow_id(page_id) if entry.tail_overflow else page_id, slot)

    def flush(self, table: str | None = None) -> None:
        targets = self._tables.values() if table is None else [self._table(table)]
        for entry in targets:
            if entry.tail is not None and entry.dirty:
                manager = entry.overflow if entry.tail_overflow else entry.manager
                manager.write_page(entry.tail.page_id, entry.tail.to_bytes())
                entry.dirty = False
        self._bridge.sync()

    def fetch(self, table: str, rid: RID) -> Record | None:
        entry = self._table(table)
        page_id, slot = rid
        page = self._page(entry, page_id)
        if page is None:
            return None
        raw = page.get_record(slot)
        return entry.codec.unpack(raw) if raw else None

    def scan(self, table: str) -> Iterator[tuple[RID, Record]]:
        entry = self._table(table)
        for page_id in range(self._allocated(entry)):
            page = self._page(entry, page_id)
            if page is None:
                continue
            for slot in range(page.record_count):
                raw = page.get_record(slot)
                if raw:
                    yield (page_id, slot), entry.codec.unpack(raw)
        if entry.ordered:
            yield from self._scan_overflow(entry)

    def delete(self, table: str, rid: RID) -> bool:
        entry = self._table(table)
        page_id, slot = rid
        page = self._page(entry, page_id)
        if page is None:
            return False
        if slot < 0 or slot >= page.record_count or page.get_record(slot) is None:
            return False
        self._tombstone(page, slot)
        if entry.tail is not None and entry.tail is page:
            entry.dirty = True
        else:
            manager = entry.overflow if page_id < 0 else entry.manager
            manager.write_page(page.page_id, page.to_bytes())
            self._bridge.sync()
        return True

    def page_count(self, table: str) -> int:
        return max(1, self._allocated(self._table(table)))

    # -- ordered access (Sequential File) -------------------------------

    def search_key(self, table: str, key) -> list[tuple[RID, Record]]:
        """Binary descent over the ordered main file, then the overflow area."""
        entry = self._require_ordered(table)
        self.flush(table)
        found = []
        page_id = self._locate_page(entry, key)
        if page_id is not None:
            for slot, record in self._records_of(entry, page_id):
                if entry.key_of(record) == key:
                    found.append(((page_id, slot), record))
        found.extend(
            (rid, record)
            for rid, record in self._scan_overflow(entry)
            if entry.key_of(record) == key
        )
        return found

    def range_key(self, table: str, lower, upper) -> Iterator[tuple[RID, Record]]:
        """The ordered main file swept from the lower bound, merged with overflow."""
        entry = self._require_ordered(table)
        self.flush(table)
        start = 0 if lower is None else (self._locate_page(entry, lower, floor=True) or 0)
        collected = []
        for page_id in range(start, entry.manager.get_total_pages()):
            page_keys = self._records_of(entry, page_id)
            if not page_keys:
                continue
            if upper is not None and entry.key_of(page_keys[0][1]) > upper:
                break
            for slot, record in page_keys:
                key = entry.key_of(record)
                if (lower is None or key >= lower) and (upper is None or key <= upper):
                    collected.append(((page_id, slot), record))
        for rid, record in self._scan_overflow(entry):
            key = entry.key_of(record)
            if (lower is None or key >= lower) and (upper is None or key <= upper):
                collected.append((rid, record))
        collected.sort(key=lambda item: entry.key_of(item[1]))
        return iter(collected)

    def reorganize(self, table: str) -> ReorganizeReport:
        """Merge overflow back into the main file, rewritten in key order.

        The main file is rebuilt at a fill factor below 100 % so the next batch
        of inserts has room in the page it belongs to instead of going straight
        to overflow.
        """
        entry = self._require_ordered(table)
        self.flush(table)
        pages_before = self._allocated(entry)
        from_overflow = 0

        records = [record for _, record in self._scan_main(entry)]
        for _, record in self._scan_overflow(entry):
            records.append(record)
            from_overflow += 1
        records.sort(key=entry.key_of)

        per_page = max(1, int(entry.schema.records_per_page * FILL_FACTOR))
        self._rewrite(entry, entry.manager, records, per_page)
        self._truncate(entry.overflow)
        entry.tail, entry.dirty = None, False

        return ReorganizeReport(
            records_kept=len(records),
            records_from_overflow=from_overflow,
            pages_before=pages_before,
            pages_after=max(1, entry.manager.get_total_pages()),
            fill_factor=FILL_FACTOR,
        )

    # -- internals ------------------------------------------------------

    def _check_page_size(self, schema: TableSchema) -> None:
        if schema.page_size == self._layer.page_size:
            return
        if not self._layer.variable_page_size:
            raise StorageUnavailableError(
                f"'{schema.name}' declara paginas de {schema.page_size} B pero la capa fisica "
                f"esta fijada en {self._layer.page_size} B. Para variar el tamano de bloque, "
                "Page y DiskManager deben aceptar page_size."
            )
        limit = self._layer.max_page_size()
        if schema.page_size > limit:
            raise StorageUnavailableError(
                f"'{schema.name}' pide paginas de {schema.page_size} B, pero el directorio de "
                f"slots direcciona hasta {limit} B"
            )

    def _new_manager(self, schema: TableSchema, suffix: str = ".bin"):
        path = self._path(schema.name, suffix)
        if self._layer.variable_page_size:
            return self._layer.DiskManager(path, page_size=schema.page_size)
        return self._layer.DiskManager(path)

    def _new_page(self, entry: _Table, page_id: int, previous: int):
        if self._layer.variable_page_size:
            return self._layer.Page(
                page_id=page_id, prev_page_id=previous, page_size=entry.schema.page_size
            )
        return self._layer.Page(page_id=page_id, prev_page_id=previous)

    def _load_page(self, entry: _Table, page_id: int, raw: bytes):
        if self._layer.variable_page_size:
            return self._layer.Page(
                page_id=page_id, raw_bytes=raw, page_size=entry.schema.page_size
            )
        return self._layer.Page(page_id=page_id, raw_bytes=raw)

    def _path(self, table: str, suffix: str = ".bin") -> str:
        return os.path.join(self._data_dir, f"{table.lower()}{suffix}")

    def _table(self, table: str) -> _Table:
        try:
            return self._tables[table.lower()]
        except KeyError:
            message = f"la tabla '{table}' no esta abierta en storage"
            raise StorageUnavailableError(message) from None

    def _allocated(self, entry: _Table) -> int:
        """Pages in the main file, counting a tail page not yet written."""
        total = entry.manager.get_total_pages()
        if entry.tail is not None and not entry.tail_overflow:
            total = max(total, entry.tail.page_id + 1)
        return total

    def _page(self, entry: _Table, page_id: int):
        """Resolve a page id, which is negative when it names an overflow page.

        The page currently being filled is served from memory; anything else is
        read from the file it belongs to.
        """
        overflow = page_id < 0
        if (
            entry.tail is not None
            and entry.tail_overflow == overflow
            and entry.tail.page_id == (_overflow_index(page_id) if overflow else page_id)
        ):
            return entry.tail
        if overflow:
            index = _overflow_index(page_id)
            if entry.overflow is None or index >= entry.overflow.get_total_pages():
                return None
            return self._read_from(entry, entry.overflow, index)
        if page_id >= entry.manager.get_total_pages():
            return None
        return self._read(entry, page_id)

    def _open_tail(self, entry: _Table) -> None:
        manager = entry.insert_manager
        entry.tail_overflow = entry.ordered
        total = manager.get_total_pages()
        if total:
            entry.tail = self._read_from(entry, manager, total - 1)
            entry.dirty = False
        else:
            self._new_tail(entry)

    def _new_tail(self, entry: _Table) -> None:
        """Start a fresh tail page without pre-allocating it on disk.

        The page layer offers ``allocate_page``, which writes a block of zeros to
        extend the file. Since the tail is always written out by ``flush``, going
        through it would write every page twice -- so the block is simply given
        the next id and materialises on disk when it is flushed.
        """
        entry.tail_overflow = entry.ordered
        if entry.tail is not None:
            page_id = entry.tail.page_id + 1
            previous = entry.tail.page_id
        else:
            page_id = entry.insert_manager.get_total_pages()
            previous = page_id - 1
        entry.tail = self._new_page(entry, page_id, previous)
        entry.dirty = True

    def _read(self, entry: _Table, page_id: int):
        return self._read_from(entry, entry.manager, page_id)

    def _read_from(self, entry: _Table, manager, page_id: int):
        raw = manager.read_page(page_id)
        self._bridge.sync()
        return self._load_page(entry, page_id, raw)

    def _tombstone(self, page, slot: int) -> None:
        """Mark a slot free by zeroing its length, the absence the page reports."""
        offset = self._layer.header_size + slot * self._layer.slot_size
        record_offset, _ = struct.unpack_from(self._layer.slot_format, page.data, offset)
        struct.pack_into(self._layer.slot_format, page.data, offset, record_offset, 0)

    # -- ordered helpers -------------------------------------------------

    def _require_ordered(self, table: str) -> _Table:
        entry = self._table(table)
        if not entry.ordered:
            raise StorageUnavailableError(
                f"'{entry.schema.name}' usa {entry.schema.storage.value} "
                "y no ofrece acceso ordenado"
            )
        if entry.key_position is None:
            raise StorageUnavailableError(
                f"'{entry.schema.name}' no declara PRIMARY KEY, asi que no hay orden"
            )
        return entry

    def _records_of(self, entry: _Table, page_id: int) -> list[tuple[int, Record]]:
        page = self._page(entry, page_id)
        if page is None:
            return []
        out = []
        for slot in range(page.record_count):
            raw = page.get_record(slot)
            if raw:
                out.append((slot, entry.codec.unpack(raw)))
        return out

    def _locate_page(self, entry: _Table, key, floor: bool = False) -> int | None:
        """Binary search for the page whose key span contains ``key``.

        With ``floor`` the search returns the page where a sweep should start
        even when the key itself is absent, which is what a range needs.
        """
        low, high = 0, entry.manager.get_total_pages() - 1
        candidate = None
        while low <= high:
            middle = (low + high) // 2
            records = self._records_of(entry, middle)
            if not records:
                high = middle - 1
                continue
            first = entry.key_of(records[0][1])
            last = entry.key_of(records[-1][1])
            if key < first:
                candidate = middle if floor else candidate
                high = middle - 1
            elif key > last:
                low = middle + 1
            else:
                return middle
        if floor:
            return candidate if candidate is not None else max(0, low - 1)
        return None

    def _scan_main(self, entry: _Table) -> Iterator[tuple[RID, Record]]:
        for page_id in range(entry.manager.get_total_pages()):
            for slot, record in self._records_of(entry, page_id):
                yield (page_id, slot), record

    def _scan_overflow(self, entry: _Table) -> Iterator[tuple[RID, Record]]:
        if entry.overflow is None:
            return
        for index in range(entry.overflow.get_total_pages()):
            page = self._read_from(entry, entry.overflow, index)
            for slot in range(page.record_count):
                raw = page.get_record(slot)
                if raw:
                    yield (_overflow_id(index), slot), entry.codec.unpack(raw)

    def _rewrite(self, entry: _Table, manager, records: list[Record], per_page: int) -> None:
        """Write records back in order, `per_page` to a block."""
        self._truncate(manager)
        page_id = 0
        for start in range(0, len(records), per_page):
            page = self._new_page(entry, page_id, page_id - 1)
            for record in records[start : start + per_page]:
                page.insert_record(entry.codec.pack(record))
            manager.write_page(page_id, page.to_bytes())
            page_id += 1
        if page_id == 0:
            page = self._new_page(entry, 0, -1)
            manager.write_page(0, page.to_bytes())
        self._bridge.sync()

    @staticmethod
    def _truncate(manager) -> None:
        if manager is None:
            return
        with open(manager.db_path, "wb"):
            pass

    def _not_ordered(self, table: str) -> StorageUnavailableError:
        schema = self._table(table).schema
        if schema.storage is StorageKind.SEQUENTIAL:
            return StorageUnavailableError(
                f"'{schema.name}' es SEQUENTIAL pero el adaptador en disco todavia no ofrece "
                "area ordenada ni overflow; usa USING HEAP o conecta el Sequential File"
            )
        return StorageUnavailableError(
            f"'{schema.name}' usa {schema.storage.value} y no ofrece acceso ordenado"
        )


def _overflow_id(index: int) -> int:
    """Overflow page `index` as a RID page id: negative, so it never collides."""
    return -(index + 1)


def _overflow_index(page_id: int) -> int:
    return -page_id - 1
