"""Sending each index to the implementation that can serve it.

Three backends can hold an index, and none of them covers every case:

* the on-disk B+ tree answers ordered ranges, but packs keys as 4-byte integers
  and treats them as unique, so it only takes integer primary keys
* the on-disk extendible hash returns every RID for a key and handles repeats,
  but has no ordered traversal, so it only takes equality lookups
* the in-memory stand-in takes anything, and is the fallback

Each index is routed once, when it is created, and the choice is remembered so
every later call reaches the same place. ``describe`` reports where each one
landed and why, which is what ``GET /api/tables`` and the report quote.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..catalog import IndexMeta, TableSchema
from .port import RID, IndexManager, IOCounter


class RoutingIndexStore:
    """IndexManager that picks a backend per index and stays consistent."""

    def __init__(
        self,
        candidates: list[tuple[str, IndexManager]],
        fallback: IndexManager,
        io: IOCounter,
    ):
        self.io = io
        self._candidates = candidates
        self._fallback = fallback
        self._routes: dict[str, IndexManager] = {}
        self._reasons: dict[str, str] = {}

    def create_index(self, meta: IndexMeta, schema: TableSchema) -> None:
        key = meta.name.lower()
        refusals = []
        for label, backend in self._candidates:
            refusal = backend.why_not(meta, schema)
            if refusal is None:
                backend.create_index(meta, schema)
                self._routes[key] = backend
                self._reasons[key] = label
                return
            refusals.append(refusal)

        self._fallback.create_index(meta, schema)
        self._routes[key] = self._fallback
        self._reasons[key] = f"sustituto en memoria: {refusals[-1] if refusals else 'sin backend'}"

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
