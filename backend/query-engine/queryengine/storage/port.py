"""The contract between the query engine and the physical storage layer.

The query engine never touches a file. It asks a ``StorageEngine`` for records
and an ``IndexManager`` for RIDs, and both report their physical cost through a
single shared ``IOCounter``.

Keeping this boundary narrow is what lets the storage, index and query modules
be built independently: any object that satisfies these protocols can be plugged
in, whether it runs in process or behind an HTTP client.

Conventions every implementation must honour:

* A RID is the pair ``(page_id, slot)`` and is stable until the record is
  deleted or the table is reorganized.
* A record is a tuple of Python values positionally matching ``schema.columns``.
* Every physical block transferred increments the injected ``IOCounter``.
  Logical operations that touch no block increment nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..catalog import IndexMeta, TableSchema

RID = tuple[int, int]
Record = tuple


@dataclass
class IOCounter:
    """The DiskCounter the assignment requires.

    A single instance is shared by the table store and the index manager so the
    figures reported for one query add up across every structure it touched.
    """

    disk_reads: int = 0
    disk_writes: int = 0

    def read(self, blocks: int = 1) -> None:
        self.disk_reads += blocks

    def write(self, blocks: int = 1) -> None:
        self.disk_writes += blocks

    def reset(self) -> None:
        self.disk_reads = 0
        self.disk_writes = 0

    def snapshot(self) -> tuple[int, int]:
        return self.disk_reads, self.disk_writes

    def since(self, mark: tuple[int, int]) -> IOCounter:
        return IOCounter(self.disk_reads - mark[0], self.disk_writes - mark[1])

    @property
    def total(self) -> int:
        return self.disk_reads + self.disk_writes

    def to_dict(self) -> dict:
        return {
            "disk_reads": self.disk_reads,
            "disk_writes": self.disk_writes,
            "total": self.total,
        }


@dataclass(frozen=True)
class ReorganizeReport:
    """What a Sequential File reorganization moved."""

    records_kept: int
    records_from_overflow: int
    pages_before: int
    pages_after: int
    fill_factor: float

    def to_dict(self) -> dict:
        return {
            "records_kept": self.records_kept,
            "records_from_overflow": self.records_from_overflow,
            "pages_before": self.pages_before,
            "pages_after": self.pages_after,
            "fill_factor": round(self.fill_factor, 4),
        }


@runtime_checkable
class StorageEngine(Protocol):
    """Physical table access: Heap File and Sequential File."""

    io: IOCounter

    def create_table(self, schema: TableSchema) -> None:
        """Allocate the backing file for a new table."""

    def drop_table(self, table: str) -> None:
        """Release the backing file and everything in it."""

    def insert(self, table: str, record: Record) -> RID:
        """Append or place one record and return the RID it landed on."""

    def fetch(self, table: str, rid: RID) -> Record | None:
        """Read a single record by RID; None when the slot is free."""

    def scan(self, table: str) -> Iterator[tuple[RID, Record]]:
        """Full table scan, one page at a time."""

    def delete(self, table: str, rid: RID) -> bool:
        """Remove one record; True when a live record was removed."""

    def page_count(self, table: str) -> int:
        """Pages currently allocated to the table, used to price a SeqScan."""

    def search_key(self, table: str, key) -> list[tuple[RID, Record]]:
        """Ordered lookup on the primary key.

        Only meaningful for SEQUENTIAL tables, where it is a binary search over
        the ordered area followed by a walk of the overflow chain. Heap tables
        may raise NotImplementedError; the planner never asks them.
        """

    def range_key(self, table: str, lower, upper) -> Iterator[tuple[RID, Record]]:
        """Ordered range scan on the primary key, bounds inclusive."""

    def reorganize(self, table: str) -> ReorganizeReport:
        """Merge the overflow area back into the ordered area."""


@runtime_checkable
class IndexManager(Protocol):
    """Secondary access paths: B+ tree and dynamic hashing."""

    io: IOCounter

    def create_index(self, meta: IndexMeta, schema: TableSchema) -> None:
        """Build an index over an existing table."""

    def drop_index(self, name: str) -> None:
        """Release the index file."""

    def insert(self, name: str, key, rid: RID) -> None:
        """Register one key/RID pair."""

    def delete(self, name: str, key, rid: RID) -> None:
        """Remove one key/RID pair."""

    def search(self, name: str, key) -> list[RID]:
        """Every RID whose key equals ``key``."""

    def range_search(self, name: str, lower, upper) -> list[RID]:
        """Every RID with ``lower <= key <= upper``. B+ tree only."""

    def height(self, name: str) -> int:
        """Levels traversed for a point lookup, used to price an IndexScan."""

    def bulk_load(self, name: str, entries: Iterable[tuple[object, RID]]) -> None:
        """Populate an index from an existing table in one pass."""
