"""Hash index access over the extendible hash file the index team owns.

``ExtendibleHashFile`` expects a ``StorageBackend`` -- allocate, read, write,
get_total_pages, a page size and a counter -- which is exactly the surface the
physical ``DiskManager`` already offers, so it is handed one directly and no
shim sits between them.

Unlike the B+ tree this one stores keys as 8-byte integers and returns every
RID for a key, so it serves repeated keys correctly. It has no ordered
traversal, which is inherent to hashing: ``range_search`` is refused and the
planner never asks a hash index for a range.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterable

from ..catalog import IndexKind, IndexMeta, TableSchema
from ..errors import StorageUnavailableError
from ..types import TypeKind
from .blk01 import PhysicalLayer, load
from .port import RID, IOCounter

INT_KEY_KINDS = (TypeKind.INT, TypeKind.BIGINT)
INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1


class _Index:
    def __init__(self, meta: IndexMeta, table, manager, path: str):
        self.meta = meta
        self.table = table
        self.manager = manager
        self.path = path


class DiskHashIndex:
    """IndexManager backed by the on-disk extendible hash."""

    def __init__(self, io: IOCounter, data_dir: str, layer: PhysicalLayer | None = None):
        self.io = io
        self._layer = layer or load()
        if self._layer.ExtendibleHash is None:
            raise StorageUnavailableError(
                "el modulo de almacenamiento no expone ExtendibleHashFile"
            )
        self._data_dir = os.path.abspath(data_dir)
        os.makedirs(self._data_dir, exist_ok=True)
        self._indexes: dict[str, _Index] = {}
        self._counters: list = []
        self._seen_reads = 0
        self._seen_writes = 0

    # -- routing decision -----------------------------------------------

    @staticmethod
    def why_not(meta: IndexMeta, schema: TableSchema) -> str | None:
        if meta.kind is not IndexKind.HASH:
            return f"{meta.kind.value} no se resuelve con hashing"
        column = schema.column(meta.column)
        if column.type.kind not in INT_KEY_KINDS:
            return (
                f"el hash empaqueta claves como enteros de 8 bytes y "
                f"{schema.name}.{column.name} es {column.type}"
            )
        return None

    def accepts(self, meta: IndexMeta, schema: TableSchema) -> bool:
        return self.why_not(meta, schema) is None

    # -- lifecycle ------------------------------------------------------

    def create_index(self, meta: IndexMeta, schema: TableSchema) -> None:
        refusal = self.why_not(meta, schema)
        if refusal is not None:
            raise StorageUnavailableError(f"'{meta.name}' no puede ir al hash: {refusal}")
        path = self._path(meta.name)
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
        manager = self._new_manager(path, schema.page_size)
        self._counters.append(manager.counter)
        table = self._layer.ExtendibleHash.create(manager)
        self._indexes[meta.name.lower()] = _Index(meta, table, manager, path)
        self._sync()

    def drop_index(self, name: str) -> None:
        entry = self._indexes.pop(name.lower(), None)
        if entry is None:
            return
        self._sync()
        if entry.manager.counter in self._counters:
            self._counters.remove(entry.manager.counter)
            self._seen_reads -= entry.manager.counter.disk_reads
            self._seen_writes -= entry.manager.counter.disk_writes
        with contextlib.suppress(FileNotFoundError):
            os.remove(entry.path)

    # -- entries --------------------------------------------------------

    def insert(self, name: str, key, rid: RID) -> None:
        entry = self._index(name)
        entry.table.insert(self._key(entry, key), (int(rid[0]), int(rid[1])))
        self._sync()

    def delete(self, name: str, key, rid: RID) -> None:
        entry = self._index(name)
        entry.table.delete(self._key(entry, key), (int(rid[0]), int(rid[1])))
        self._sync()

    def search(self, name: str, key) -> list[RID]:
        entry = self._index(name)
        try:
            packed = self._key(entry, key)
        except StorageUnavailableError:
            return []
        found = entry.table.search(packed)
        self._sync()
        return [tuple(rid) for rid in found]

    def range_search(self, name: str, lower, upper) -> list[RID]:
        raise StorageUnavailableError(
            f"'{name}' es un indice hash y no admite busqueda por rango"
        )

    def height(self, name: str) -> int:
        """Blocks a probe reads: the bucket, plus the header the file re-reads.

        Extendible hashing reaches a bucket through an in-memory directory, so
        the descent is constant and does not grow with the data.
        """
        return 2

    def bulk_load(self, name: str, entries: Iterable[tuple[object, RID]]) -> None:
        entry = self._index(name)
        for key, rid in entries:
            entry.table.insert(self._key(entry, key), (int(rid[0]), int(rid[1])))
        self._sync()

    # -- internals ------------------------------------------------------

    def _path(self, name: str) -> str:
        return os.path.join(self._data_dir, f"{name.lower()}.hash")

    def _new_manager(self, path: str, page_size: int):
        if self._layer.variable_page_size:
            return self._layer.DiskManager(path, page_size=page_size)
        return self._layer.DiskManager(path)

    def _index(self, name: str) -> _Index:
        try:
            return self._indexes[name.lower()]
        except KeyError:
            message = f"el indice '{name}' no esta abierto en storage"
            raise StorageUnavailableError(message) from None

    @staticmethod
    def _key(entry: _Index, key) -> int:
        if isinstance(key, bool) or not isinstance(key, int):
            raise StorageUnavailableError(
                f"'{entry.meta.name}' indexa enteros y recibio {key!r}"
            )
        if not INT64_MIN <= key <= INT64_MAX:
            raise StorageUnavailableError(f"la clave {key} no entra en 8 bytes con signo")
        return key

    def _sync(self) -> None:
        reads = sum(counter.disk_reads for counter in self._counters)
        writes = sum(counter.disk_writes for counter in self._counters)
        self.io.disk_reads += reads - self._seen_reads
        self.io.disk_writes += writes - self._seen_writes
        self._seen_reads, self._seen_writes = reads, writes
