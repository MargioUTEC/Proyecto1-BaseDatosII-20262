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

Sequential File organisation is a separate deliverable and is not provided
here; a SEQUENTIAL table reports that plainly instead of pretending to be
ordered. Heap tables are fully functional on disk.
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


class _Table:
    def __init__(self, schema: TableSchema, manager, codec: RecordCodec):
        self.schema = schema
        self.manager = manager
        self.codec = codec
        self.tail = None      # the page inserts are currently filling
        self.dirty = False


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
        if schema.page_size != self._layer.page_size:
            raise StorageUnavailableError(
                f"'{schema.name}' declara paginas de {schema.page_size} B pero la capa fisica "
                f"esta compilada para {self._layer.page_size} B. Para variar el tamano de bloque "
                "el modulo de almacenamiento debe recibirlo como parametro, no como constante."
            )
        codec = RecordCodec(schema)
        capacity = self._layer.page_size - self._layer.header_size
        if codec.size + self._layer.slot_size > capacity:
            raise StorageUnavailableError(
                f"un registro de '{schema.name}' ocupa {codec.size} bytes y no entra en una "
                f"pagina de {self._layer.page_size} bytes"
            )
        manager = self._layer.DiskManager(self._path(schema.name))
        self._bridge.attach(manager.counter)
        self._tables[key] = _Table(schema, manager, codec)
        self._bridge.sync()

    def drop_table(self, table: str) -> None:
        entry = self._tables.pop(table.lower(), None)
        if entry is None:
            return
        entry.tail, entry.dirty = None, False
        self._bridge.retire(entry.manager.counter)
        with contextlib.suppress(FileNotFoundError):
            os.remove(self._path(entry.schema.name))

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
        return (entry.tail.page_id, slot)

    def flush(self, table: str | None = None) -> None:
        targets = self._tables.values() if table is None else [self._table(table)]
        for entry in targets:
            if entry.tail is not None and entry.dirty:
                entry.manager.write_page(entry.tail.page_id, entry.tail.to_bytes())
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

    def delete(self, table: str, rid: RID) -> bool:
        entry = self._table(table)
        page_id, slot = rid
        page = self._page(entry, page_id)
        if page is None:
            return False
        if slot < 0 or slot >= page.record_count or page.get_record(slot) is None:
            return False
        self._tombstone(page, slot)
        if entry.tail is not None and entry.tail.page_id == page_id:
            entry.dirty = True
        else:
            entry.manager.write_page(page_id, page.to_bytes())
            self._bridge.sync()
        return True

    def page_count(self, table: str) -> int:
        return max(1, self._allocated(self._table(table)))

    # -- ordered access (Sequential File) -------------------------------

    def search_key(self, table: str, key) -> list[tuple[RID, Record]]:
        raise self._not_ordered(table)

    def range_key(self, table: str, lower, upper) -> Iterator[tuple[RID, Record]]:
        raise self._not_ordered(table)

    def reorganize(self, table: str) -> ReorganizeReport:
        raise self._not_ordered(table)

    # -- internals ------------------------------------------------------

    def _path(self, table: str) -> str:
        return os.path.join(self._data_dir, f"{table.lower()}.bin")

    def _table(self, table: str) -> _Table:
        try:
            return self._tables[table.lower()]
        except KeyError:
            message = f"la tabla '{table}' no esta abierta en storage"
            raise StorageUnavailableError(message) from None

    def _allocated(self, entry: _Table) -> int:
        """Pages in the file, counting a tail page that has not been written yet."""
        total = entry.manager.get_total_pages()
        if entry.tail is not None:
            total = max(total, entry.tail.page_id + 1)
        return total

    def _page(self, entry: _Table, page_id: int):
        """The tail page comes from memory; anything else from disk."""
        if entry.tail is not None and entry.tail.page_id == page_id:
            return entry.tail
        if page_id < 0 or page_id >= entry.manager.get_total_pages():
            return None
        return self._read(entry, page_id)

    def _open_tail(self, entry: _Table) -> None:
        total = entry.manager.get_total_pages()
        if total:
            entry.tail = self._read(entry, total - 1)
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
        if entry.tail is not None:
            page_id = entry.tail.page_id + 1
            previous = entry.tail.page_id
        else:
            page_id = entry.manager.get_total_pages()
            previous = page_id - 1
        entry.tail = self._layer.Page(page_id=page_id, prev_page_id=previous)
        entry.dirty = True

    def _read(self, entry: _Table, page_id: int):
        raw = entry.manager.read_page(page_id)
        self._bridge.sync()
        return self._layer.Page(page_id=page_id, raw_bytes=raw)

    def _tombstone(self, page, slot: int) -> None:
        """Mark a slot free by zeroing its length, the absence the page reports."""
        offset = self._layer.header_size + slot * self._layer.slot_size
        record_offset, _ = struct.unpack_from(self._layer.slot_format, page.data, offset)
        struct.pack_into(self._layer.slot_format, page.data, offset, record_offset, 0)

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
