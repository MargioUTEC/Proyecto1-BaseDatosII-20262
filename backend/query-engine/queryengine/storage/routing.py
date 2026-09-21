"""Sending each index to the implementation that can serve it.

The on-disk B+ tree covers integer primary keys, which is what every access
path in the experiments uses. It has no answer yet for hash indexes, for
non-integer key columns, or for columns whose values repeat -- it treats keys
as unique and would drop the duplicates.

Rather than let the engine lose rows or refuse those indexes outright, each one
is routed at creation time to the backend that can hold it, and the choice is
remembered so every later call reaches the same place. ``describe`` reports
where each index landed and why, which is what the README and the report quote.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..catalog import IndexMeta, TableSchema
from .port import RID, IndexManager, IOCounter


class RoutingIndexStore:
    """IndexManager that picks a backend per index and stays consistent."""

    def __init__(self, primary: IndexManager, fallback: IndexManager, io: IOCounter):
        self.io = io
        self._primary = primary
        self._fallback = fallback
        self._routes: dict[str, IndexManager] = {}
        self._reasons: dict[str, str] = {}

    def create_index(self, meta: IndexMeta, schema: TableSchema) -> None:
        key = meta.name.lower()
        refusal = self._primary.why_not(meta, schema)
        if refusal is None:
            self._primary.create_index(meta, schema)
            self._routes[key] = self._primary
            self._reasons[key] = "arbol B+ en disco"
        else:
            self._fallback.create_index(meta, schema)
            self._routes[key] = self._fallback
            self._reasons[key] = f"sustituto en memoria: {refusal}"

    def drop_index(self, name: str) -> None:
        key = name.lower()
        backend = self._routes.pop(key, None)
        self._reasons.pop(key, None)
        if backend is not None:
            backend.drop_index(name)

    def insert(self, name: str, key, rid: RID) -> None:
        self._route(name).insert(name, key, rid)

    def delete(self, name: str, key, rid: RID) -> None:
        self._route(name).delete(name, key, rid)

    def search(self, name: str, key) -> list[RID]:
        return self._route(name).search(name, key)

    def range_search(self, name: str, lower, upper) -> list[RID]:
        return self._route(name).range_search(name, lower, upper)

    def height(self, name: str) -> int:
        return self._route(name).height(name)

    def bulk_load(self, name: str, entries: Iterable[tuple[object, RID]]) -> None:
        self._route(name).bulk_load(name, entries)

    def describe(self) -> dict[str, str]:
        """Where each index lives, for the catalog listing and the report."""
        return dict(self._reasons)

    def _route(self, name: str) -> IndexManager:
        return self._routes.get(name.lower(), self._fallback)
