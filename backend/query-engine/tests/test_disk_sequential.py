"""Sequential File on real blocks: ordered main area, overflow, reorganize."""

import os

import pytest

from queryengine.bootstrap import Settings, build_engine
from queryengine.errors import StorageUnavailableError

blk01 = pytest.importorskip("queryengine.storage.blk01")

try:
    blk01.load()
except StorageUnavailableError as exc:  # pragma: no cover - depends on checkout
    pytest.skip(f"capa fisica no disponible: {exc}", allow_module_level=True)

SCHEMA = "(id INT PRIMARY KEY, nombre CHAR(20), pais CHAR(16))"


@pytest.fixture
def engine(tmp_path):
    return build_engine(
        Settings(
            backend="disk",
            catalog_path=str(tmp_path / "catalog.json"),
            data_dir=str(tmp_path),
            table_dir=str(tmp_path / "tables"),
        )
    )


@pytest.fixture
def ordered(engine):
    engine.execute(f"CREATE TABLE t {SCHEMA} USING SEQUENTIAL")
    values = ", ".join(f"({n}, 'n{n}', 'pais-{n % 7}')" for n in range(1500))
    engine.execute(f"INSERT INTO t VALUES {values}")
    return engine


def test_every_row_comes_back(ordered):
    assert ordered.execute("SELECT * FROM t").row_count == 1500


def test_point_lookup_uses_a_binary_search(ordered):
    result = ordered.execute("SELECT nombre FROM t WHERE id = 900")
    assert "SequentialSearch" in result.plan_text
    assert result.rows == [("n900",)]


def test_a_range_sweeps_in_key_order(ordered):
    result = ordered.execute("SELECT id FROM t WHERE id >= 100 AND id <= 109")
    assert "SequentialRangeScan" in result.plan_text
    assert [row[0] for row in result.rows] == list(range(100, 110))


def test_reorganize_folds_the_overflow_back(ordered, tmp_path):
    report = ordered.reorganize("t")["report"]
    assert report["records_kept"] == 1500
    assert report["records_from_overflow"] == 1500
    assert report["fill_factor"] == 0.75
    assert os.path.getsize(tmp_path / "tables" / "t.ovf") == 0


def test_rows_survive_the_reorganization(ordered):
    ordered.reorganize("t")
    assert ordered.execute("SELECT * FROM t").row_count == 1500
    assert ordered.execute("SELECT nombre FROM t WHERE id = 1234").rows == [("n1234",)]


def test_after_reorganizing_a_lookup_reads_far_fewer_blocks(ordered):
    before = ordered.execute("SELECT * FROM t WHERE id = 1234").metrics.disk_reads
    ordered.reorganize("t")
    after = ordered.execute("SELECT * FROM t WHERE id = 1234").metrics.disk_reads
    assert after < before


def test_inserts_after_a_reorganization_land_in_overflow(ordered, tmp_path):
    ordered.reorganize("t")
    ordered.execute("INSERT INTO t VALUES (99999, 'tardio', 'pais-1')")
    assert os.path.getsize(tmp_path / "tables" / "t.ovf") > 0
    assert ordered.execute("SELECT nombre FROM t WHERE id = 99999").rows == [("tardio",)]


def test_a_range_spanning_main_and_overflow_stays_sorted(ordered):
    ordered.reorganize("t")
    ordered.execute("INSERT INTO t VALUES (555555, 'z', 'p'), (2, 'dup', 'p')")
    rows = ordered.execute("SELECT id FROM t WHERE id >= 0 AND id <= 10").rows
    keys = [row[0] for row in rows]
    assert keys == sorted(keys)


def test_delete_works_in_both_areas(ordered):
    ordered.execute("DELETE FROM t WHERE id = 700")
    assert ordered.execute("SELECT * FROM t WHERE id = 700").row_count == 0
    ordered.reorganize("t")
    ordered.execute("DELETE FROM t WHERE id = 701")
    assert ordered.execute("SELECT * FROM t WHERE id = 701").row_count == 0
    assert ordered.execute("SELECT * FROM t").row_count == 1498


def test_heap_tables_still_refuse_ordered_access(engine):
    from queryengine.errors import CatalogError

    engine.execute(f"CREATE TABLE h {SCHEMA} USING HEAP")
    with pytest.raises(CatalogError):
        engine.reorganize("h")
