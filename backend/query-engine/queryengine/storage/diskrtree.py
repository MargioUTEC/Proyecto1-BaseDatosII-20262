"""R-Tree adapter that stores real table RIDs in fixed-size index pages."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from collections.abc import Iterable

from ..catalog import IndexKind, IndexMeta, TableSchema
from ..errors import StorageUnavailableError
from ..spatial import parse_point
from .port import IOCounter, RID
from ..types import TypeKind


class DiskRTreeIndex:
    def __init__(self, io: IOCounter, data_dir: str):
        self.io = io
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self._trees = {}

    def why_not(self, meta: IndexMeta, schema: TableSchema) -> str | None:
        if meta.kind is not IndexKind.RTREE:
            return "no es RTREE"
        if schema.column(meta.column).type.kind not in (TypeKind.CHAR, TypeKind.VARCHAR):
            return f"{schema.name}.{meta.column} debe almacenar POINT(lon lat) en CHAR/VARCHAR"
        return None

    def accepts(self, meta: IndexMeta, schema: TableSchema) -> bool:
        return self.why_not(meta, schema) is None

    def create_index(self, meta: IndexMeta, schema: TableSchema) -> None:
        refusal = self.why_not(meta, schema)
        if refusal:
            raise StorageUnavailableError(refusal)
        path = self._path(meta.name)
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
        self._open(meta, path)

    def reopen_index(self, meta: IndexMeta, schema: TableSchema) -> None:
        refusal = self.why_not(meta, schema)
        if refusal:
            raise StorageUnavailableError(refusal)
        self._open(meta, self._path(meta.name))

    def _open(self, meta: IndexMeta, path: str) -> None:
        root = next((str(parent) for parent in Path(__file__).resolve().parents
                    if (parent / "rtree.py").is_file()), None)
        if root is None:
            raise StorageUnavailableError("no se encuentra rtree.py en el despliegue")
        if root not in sys.path:
            sys.path.insert(0, root)
        from rtree import RTree

        self._trees[meta.name.lower()] = (meta, RTree(path, D=2, dtype="d", page_size=4096, counter=self.io))

    def drop_index(self, name: str) -> None:
        item = self._trees.pop(name.lower(), None)
        if item is not None:
            tree = item[1]
            tree.close()
            with contextlib.suppress(FileNotFoundError):
                os.remove(tree.f.name)

    def insert(self, name: str, key, rid: RID) -> None:
        self._entry(name).insert_point(parse_point(key), rid)

    def delete(self, name: str, key, rid: RID) -> None:
        # Stale leaf entries are harmless: index scans validate each RID through
        # the table, which returns None for a deleted slot.
        return None

    def search(self, name: str, key) -> list[RID]:
        point = parse_point(key)
        window = point + point
        return [rid for rid, _ in self._entry(name).search_window(window)]

    def range_search(self, name: str, lower, upper) -> list[RID]:
        raise StorageUnavailableError("RTREE requiere búsqueda espacial")

    def height(self, name: str) -> int:
        return max(1, self._entry(name).height)

    def entry_count(self, name: str) -> int:
        return self._entry(name).count

    def bulk_load(self, name: str, entries: Iterable[tuple[object, RID]]) -> None:
        tree = self._entry(name)
        for key, rid in entries:
            tree.insert_point(parse_point(key), rid)

    def search_radius(self, name: str, point, radius: float):
        return self._entry(name).search_radius(point, radius)

    def search_window(self, name: str, window):
        return self._entry(name).search_window(window)

    def knn(self, name: str, point, k: int):
        return self._entry(name).knn(point, k)

    def _entry(self, name: str):
        try:
            return self._trees[name.lower()][1]
        except KeyError:
            raise StorageUnavailableError(f"R-Tree '{name}' no esta abierto") from None

    def _path(self, name: str) -> str:
        return os.path.join(self.data_dir, f"{name.lower()}.rtree")
