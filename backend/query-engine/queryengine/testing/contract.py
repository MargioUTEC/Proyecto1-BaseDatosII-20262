"""Conformance suites for the storage and index ports.

Any implementation of ``StorageEngine`` or ``IndexManager`` can be checked
against the behaviour the query engine relies on by subclassing these and
providing the one fixture that builds it:

    from queryengine.testing import StorageEngineContract

    class TestMyHeapFile(StorageEngineContract):
        @pytest.fixture
        def store(self, tmp_path):
            io = IOCounter()
            return MyHeapFile(io, str(tmp_path))

Every test here states a rule the planner or the executor depends on. A failure
is not a style disagreement: it is a case where the engine would return wrong
rows or report the wrong cost.
"""

from __future__ import annotations

import pytest

from ..catalog import Column, IndexKind, IndexMeta, StorageKind, TableSchema
from ..types import TypeKind, parse_type

SCHEMA = TableSchema(
    name="contract_table",
    columns=(
        Column("id", parse_type("INT"), primary_key=True, nullable=False),
        Column("nombre", parse_type("CHAR", 20)),
        Column("monto", parse_type("FLOAT")),
    ),
    storage=StorageKind.HEAP,
)

ROWS = [
    (1, "Ada", 10.5),
    (2, "Grace", 20.25),
    (3, "Barbara", 30.0),
    (4, "Edgar", 40.75),
]


class StorageEngineContract:
    """What the executor assumes about any table store."""

    @pytest.fixture
    def store(self):
        raise NotImplementedError("define a 'store' fixture returning a StorageEngine")

    @pytest.fixture
    def loaded(self, store):
        store.create_table(SCHEMA)
        rids = [store.insert(SCHEMA.name, row) for row in ROWS]
        return store, rids

    def test_insert_returns_a_two_part_rid(self, store):
        store.create_table(SCHEMA)
        rid = store.insert(SCHEMA.name, ROWS[0])
        assert isinstance(rid, tuple) and len(rid) == 2
        assert all(isinstance(part, int) for part in rid)

    def test_rids_are_unique(self, loaded):
        _, rids = loaded
        assert len(set(rids)) == len(rids)

    def test_fetch_returns_what_was_inserted(self, loaded):
        store, rids = loaded
        for rid, row in zip(rids, ROWS, strict=False):
            assert store.fetch(SCHEMA.name, rid) == row

    def test_scan_yields_every_live_record(self, loaded):
        store, _ = loaded
        found = [record for _, record in store.scan(SCHEMA.name)]
        assert sorted(found) == sorted(ROWS)

    def test_scan_yields_the_rid_that_fetches_the_same_record(self, loaded):
        store, _ = loaded
        for rid, record in store.scan(SCHEMA.name):
            assert store.fetch(SCHEMA.name, rid) == record

    def test_delete_removes_only_its_record(self, loaded):
        store, rids = loaded
        assert store.delete(SCHEMA.name, rids[1]) is True
        remaining = [record for _, record in store.scan(SCHEMA.name)]
        assert sorted(remaining) == sorted([ROWS[0], ROWS[2], ROWS[3]])

    def test_deleting_twice_reports_the_second_as_a_miss(self, loaded):
        store, rids = loaded
        store.delete(SCHEMA.name, rids[0])
        assert store.delete(SCHEMA.name, rids[0]) is False

    def test_fetching_a_deleted_rid_returns_none(self, loaded):
        store, rids = loaded
        store.delete(SCHEMA.name, rids[0])
        assert store.fetch(SCHEMA.name, rids[0]) is None

    def test_surviving_rids_stay_valid_after_a_delete(self, loaded):
        """The executor holds RIDs across a delete; they must not shift."""
        store, rids = loaded
        store.delete(SCHEMA.name, rids[1])
        assert store.fetch(SCHEMA.name, rids[2]) == ROWS[2]

    def test_null_survives_a_round_trip(self, store):
        store.create_table(SCHEMA)
        rid = store.insert(SCHEMA.name, (9, None, None))
        assert store.fetch(SCHEMA.name, rid) == (9, None, None)

    def test_text_is_not_padded_on_the_way_back(self, store):
        store.create_table(SCHEMA)
        rid = store.insert(SCHEMA.name, (10, "corto", 1.0))
        assert store.fetch(SCHEMA.name, rid)[1] == "corto"

    def test_records_spill_onto_further_pages(self, store):
        """More records than fit in one page must still all come back."""
        store.create_table(SCHEMA)
        count = SCHEMA.records_per_page * 2 + 5
        for number in range(count):
            store.insert(SCHEMA.name, (number, f"n{number}", float(number)))
        assert len(list(store.scan(SCHEMA.name))) == count
        assert store.page_count(SCHEMA.name) > 1

    def test_page_count_is_at_least_one(self, store):
        store.create_table(SCHEMA)
        assert store.page_count(SCHEMA.name) >= 1

    def test_flush_keeps_every_record_readable(self, loaded):
        """Buffering the tail page is fine; losing a record to it is not."""
        store, rids = loaded
        store.flush(SCHEMA.name)
        for rid, row in zip(rids, ROWS, strict=False):
            assert store.fetch(SCHEMA.name, rid) == row

    def test_flush_without_a_table_flushes_everything(self, loaded):
        store, _ = loaded
        store.flush()
        assert len(list(store.scan(SCHEMA.name))) == len(ROWS)

    def test_every_operation_moves_the_counter(self, store):
        """Reading data that is really on disk has to be counted.

        A store may buffer the page it is filling, and serving that page back
        from memory is not a disk read -- so the table here spans more than one
        page, where at least one read is unavoidable.
        """
        store.create_table(SCHEMA)
        for number in range(SCHEMA.records_per_page + 5):
            store.insert(SCHEMA.name, (number, f"n{number}", float(number)))
        store.flush(SCHEMA.name)
        assert store.io.disk_writes > 0

        before = store.io.disk_reads
        list(store.scan(SCHEMA.name))
        assert store.io.disk_reads > before

    def test_the_counter_never_goes_backwards(self, loaded):
        store, rids = loaded
        mark = store.io.snapshot()
        store.fetch(SCHEMA.name, rids[0])
        list(store.scan(SCHEMA.name))
        delta = store.io.since(mark)
        assert delta.disk_reads >= 0 and delta.disk_writes >= 0


class IndexManagerContract:
    """What the planner and the executor assume about any index."""

    kind: IndexKind = IndexKind.BTREE

    @pytest.fixture
    def indexes(self):
        raise NotImplementedError("define an 'indexes' fixture returning an IndexManager")

    @pytest.fixture
    def meta(self):
        return IndexMeta(name="ix_contract", table=SCHEMA.name, column="id", kind=self.kind)

    @pytest.fixture
    def built(self, indexes, meta):
        indexes.create_index(meta, SCHEMA)
        entries = [(number, (number // 10, number % 10)) for number in range(100)]
        indexes.bulk_load(meta.name, entries)
        return indexes, meta, dict(entries)

    def test_search_finds_the_rid_that_was_registered(self, built):
        indexes, meta, entries = built
        assert indexes.search(meta.name, 42) == [entries[42]]

    def test_search_for_a_missing_key_is_empty_not_an_error(self, built):
        indexes, meta, _ = built
        assert indexes.search(meta.name, 10_000) == []

    def test_duplicate_keys_return_every_rid(self, indexes, meta):
        indexes.create_index(meta, SCHEMA)
        indexes.insert(meta.name, 7, (0, 1))
        indexes.insert(meta.name, 7, (0, 2))
        assert sorted(indexes.search(meta.name, 7)) == [(0, 1), (0, 2)]

    def test_delete_removes_only_that_pair(self, indexes, meta):
        indexes.create_index(meta, SCHEMA)
        indexes.insert(meta.name, 7, (0, 1))
        indexes.insert(meta.name, 7, (0, 2))
        indexes.delete(meta.name, 7, (0, 1))
        assert indexes.search(meta.name, 7) == [(0, 2)]

    def test_height_is_a_positive_number_of_levels(self, built):
        indexes, meta, _ = built
        assert indexes.height(meta.name) >= 1

    def test_every_operation_moves_the_counter(self, indexes, meta):
        indexes.create_index(meta, SCHEMA)
        before = indexes.io.snapshot()
        indexes.insert(meta.name, 1, (0, 0))
        indexes.search(meta.name, 1)
        delta = indexes.io.since(before)
        assert delta.total > 0


class RangeIndexContract(IndexManagerContract):
    """Extra rules a B+ tree must satisfy; a hash index is exempt."""

    kind = IndexKind.BTREE

    def test_range_search_is_inclusive_on_both_ends(self, built):
        indexes, meta, entries = built
        found = indexes.range_search(meta.name, 10, 14)
        assert sorted(found) == sorted(entries[key] for key in range(10, 15))

    def test_an_empty_range_returns_nothing(self, built):
        indexes, meta, _ = built
        assert indexes.range_search(meta.name, 500, 600) == []

    def test_a_range_of_one_key_returns_one_rid(self, built):
        indexes, meta, entries = built
        assert indexes.range_search(meta.name, 33, 33) == [entries[33]]

    def test_range_search_covers_the_whole_index(self, built):
        indexes, meta, entries = built
        assert len(indexes.range_search(meta.name, 0, 99)) == len(entries)


__all__ = [
    "SCHEMA",
    "ROWS",
    "IndexManagerContract",
    "RangeIndexContract",
    "StorageEngineContract",
    "TypeKind",
]
