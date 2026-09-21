"""Varying the block size on real files, which is what experiment 4 measures.

Skipped when the physical storage module is absent, or when its block size is
still a module constant rather than a parameter.
"""

import os

import pytest

from queryengine.bootstrap import Settings, build_engine
from queryengine.errors import StorageUnavailableError

blk01 = pytest.importorskip("queryengine.storage.blk01")

try:
    LAYER = blk01.load()
except StorageUnavailableError as exc:  # pragma: no cover - depends on checkout
    pytest.skip(f"capa fisica no disponible: {exc}", allow_module_level=True)

if not LAYER.variable_page_size:  # pragma: no cover - depends on checkout
    pytest.skip("la capa fisica fija el tamano de bloque", allow_module_level=True)

PAGE_SIZES = [1024, 2048, 4096, 8192]


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


@pytest.mark.parametrize("page_size", PAGE_SIZES)
def test_blocks_on_disk_have_the_declared_size(engine, tmp_path, page_size):
    engine.execute(
        f"CREATE TABLE t (id INT PRIMARY KEY, nombre CHAR(20)) WITH (PAGE_SIZE = {page_size})"
    )
    values = ", ".join(f"({n}, 'n{n}')" for n in range(400))
    engine.execute(f"INSERT INTO t VALUES {values}")

    size = os.path.getsize(tmp_path / "tables" / "t.bin")
    assert size % page_size == 0
    assert size // page_size == engine.tables()[0]["page_count"]


@pytest.mark.parametrize("page_size", PAGE_SIZES)
def test_rows_survive_a_round_trip_at_any_block_size(engine, page_size):
    engine.execute(
        f"CREATE TABLE t (id INT PRIMARY KEY, nombre CHAR(20)) WITH (PAGE_SIZE = {page_size})"
    )
    values = ", ".join(f"({n}, 'fila-{n}')" for n in range(400))
    engine.execute(f"INSERT INTO t VALUES {values}")
    assert engine.execute("SELECT * FROM t").row_count == 400
    assert engine.execute("SELECT nombre FROM t WHERE id = 399").rows == [("fila-399",)]


def test_a_larger_block_holds_more_records_and_costs_fewer_reads(engine):
    reads = {}
    for page_size in (1024, 8192):
        engine.execute(
            f"CREATE TABLE t{page_size} (id INT PRIMARY KEY, nombre CHAR(20)) "
            f"WITH (PAGE_SIZE = {page_size})"
        )
        values = ", ".join(f"({n}, 'n{n}')" for n in range(2000))
        engine.execute(f"INSERT INTO t{page_size} VALUES {values}")
        result = engine.execute(f"SELECT * FROM t{page_size} WHERE id = 1999")
        reads[page_size] = result.metrics.disk_reads

    tables = {item["name"]: item for item in engine.tables()}
    assert tables["t8192"]["records_per_page"] > tables["t1024"]["records_per_page"]
    assert reads[8192] < reads[1024]


def test_a_block_beyond_the_slot_directory_is_refused(engine):
    with pytest.raises(StorageUnavailableError):
        engine.execute("CREATE TABLE huge (id INT PRIMARY KEY) WITH (PAGE_SIZE = 131072)")
