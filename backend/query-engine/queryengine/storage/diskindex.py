"""Index access over the B+ tree the index team owns.

Binds ``BPlusTree`` from the storage module to the ``IndexManager`` contract.
One tree file per index, each with its own ``DiskManager`` so the block
transfers it performs land in the shared DiskCounter alongside the table's.

The tree stores keys as 4-byte signed integers and treats them as unique, which
is exactly what a primary key index needs and is narrower than the contract
allows in general. Both limits are checked up front and reported plainly rather
than discovered as wrong results later:

* a non-integer key column is refused at CREATE INDEX
* a duplicate key is refused at insert time, because the tree drops it silently

Hash indexes have no on-disk implementation yet, so ``create_index`` refuses
them here and the engine keeps them on the in-memory stand-in.
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
INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1


class _Index:
    def __init__(self, meta: IndexMeta, tree, manager, path: str):
        self.meta = meta
        self.tree = tree
        self.manager = manager
        self.path = path
        self.height = 1
        self.height_at_pages = -1


class DiskIndexStore:
    """IndexManager backed by the on-disk B+ tree."""

    def __init__(self, io: IOCounter, data_dir: str, layer: PhysicalLayer | None = None):
        self.io = io
        self._layer = layer or load()
        if self._layer.BPlusTree is None:
            raise StorageUnavailableError(
                "el modulo de almacenamiento no expone BPlusTree; "
                "usa el backend de indices en memoria"
            )
        self._data_dir = os.path.abspath(data_dir)
        os.makedirs(self._data_dir, exist_ok=True)
        self._indexes: dict[str, _Index] = {}
        self._counters: list = []
        self._seen_reads = 0
        self._seen_writes = 0

    # -- lifecycle ------------------------------------------------------

    @staticmethod
    def why_not(meta: IndexMeta, schema: TableSchema) -> str | None:
        """Why this index cannot live in the on-disk tree, or None if it can.

        Asked before creating anything, so an index the tree cannot serve is
        routed elsewhere instead of failing halfway through a load.
        """
        if meta.kind is not IndexKind.BTREE:
            return f"{meta.kind.value} no tiene implementacion en disco todavia"
        column = schema.column(meta.column)
        if column.type.kind not in INT_KEY_KINDS:
            return (
                f"el arbol empaqueta claves como enteros de 4 bytes y "
                f"{schema.name}.{column.name} es {column.type}"
            )
        if not column.primary_key:
            return (
                f"el arbol descarta claves repetidas y {schema.name}.{column.name} "
                "no es PRIMARY KEY, asi que no se garantiza que sean unicas"
            )
        return None

    def accepts(self, meta: IndexMeta, schema: TableSchema) -> bool:
        return self.why_not(meta, schema) is None

    def create_index(self, meta: IndexMeta, schema: TableSchema) -> None:
        refusal = self.why_not(meta, schema)
        if refusal is not None:
            raise StorageUnavailableError(f"'{meta.name}' no puede ir al arbol B+: {refusal}")
        path = self._path(meta.name)
        manager = self._new_manager(path, schema.page_size)
        self._counters.append(manager.counter)
        entry = _Index(meta, self._layer.BPlusTree(manager), manager, path)
        self._indexes[meta.name.lower()] = entry
        self._refresh_height(entry)
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
        packed = self._key(entry, key)
        if entry.tree.search(packed) is not None:
            raise StorageUnavailableError(
                f"'{name}' ya contiene la clave {key!r}: el arbol B+ trata las claves como "
                "unicas y descartaria la entrada en silencio"
            )
        entry.tree.insert(packed, (int(rid[0]), int(rid[1])))
        self._sync()

    def delete(self, name: str, key, rid: RID) -> None:
        entry = self._index(name)
        packed = self._key(entry, key)
        found = entry.tree.search(packed)
        if found is None or tuple(found) != (int(rid[0]), int(rid[1])):
            self._sync()
            return
        entry.tree.remove(packed)
        self._sync()

    def search(self, name: str, key) -> list[RID]:
        entry = self._index(name)
        try:
            packed = self._key(entry, key)
        except StorageUnavailableError:
            return []  # a key outside the tree's domain simply matches nothing
        found = entry.tree.search(packed)
        self._sync()
        return [tuple(found)] if found is not None else []

    def range_search(self, name: str, lower, upper) -> list[RID]:
        entry = self._index(name)
        low = INT32_MIN if lower is None else self._clamp(lower)
        high = INT32_MAX if upper is None else self._clamp(upper)
        if low > high:
            return []
        found = entry.tree.rangeSearch(low, high)
        self._sync()
        return [tuple(rid) for rid in found]

    def height(self, name: str) -> int:
        """Blocks a point lookup reads to reach a leaf.

        Not the abstract number of levels: the tree keeps its root pointer on a
        metadata page and reads it on every descent, so that block counts too.
        Reporting what the descent really costs is what makes the plan's
        estimate match the DiskCounter afterwards.

        Planning must not perform I/O of its own -- if it did, every indexed
        query would measure more transfers than its own plan predicted. The
        value is cached and only recomputed when the index file has grown,
        which is checked with a file size lookup and moves no block.
        """
        entry = self._index(name)
        pages = entry.manager.get_total_pages()
        if pages != entry.height_at_pages:
            entry.height = self._measure(entry)
            entry.height_at_pages = pages
        return entry.height

    def _measure(self, entry: _Index) -> int:
        root = entry.tree._read_root()
        if root == -1:
            self._sync()
            return 1
        levels = 1  # the metadata page holding the root pointer
        node = entry.tree._read_node(root)
        while node is not None:
            levels += 1
            if node["is_leaf"]:
                break
            node = entry.tree._read_node(node["children"][0])
        self._sync()
        return levels

    def bulk_load(self, name: str, entries: Iterable[tuple[object, RID]]) -> None:
        entry = self._index(name)
        for key, rid in entries:
            entry.tree.insert(self._key(entry, key), (int(rid[0]), int(rid[1])))
        self._refresh_height(entry)
        self._sync()

    def _refresh_height(self, entry: _Index) -> None:
        """Price the descent now, while the tree is already being written."""
        entry.height = self._measure(entry)
        entry.height_at_pages = entry.manager.get_total_pages()

    # -- internals ------------------------------------------------------

    def _path(self, name: str) -> str:
        return os.path.join(self._data_dir, f"{name.lower()}.idx")

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
        if not INT32_MIN <= key <= INT32_MAX:
            raise StorageUnavailableError(
                f"la clave {key} no entra en los 4 bytes con signo que usa el arbol B+"
            )
        return key

    @staticmethod
    def _clamp(value) -> int:
        """Clip an open bound into the range the tree can express."""
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise StorageUnavailableError(f"limite de rango no numerico: {value!r}")
        return max(INT32_MIN, min(INT32_MAX, int(value)))

    def _sync(self) -> None:
        """Contribute what the index files moved since the last look."""
        reads = sum(counter.disk_reads for counter in self._counters)
        writes = sum(counter.disk_writes for counter in self._counters)
        self.io.disk_reads += reads - self._seen_reads
        self.io.disk_writes += writes - self._seen_writes
        self._seen_reads, self._seen_writes = reads, writes
