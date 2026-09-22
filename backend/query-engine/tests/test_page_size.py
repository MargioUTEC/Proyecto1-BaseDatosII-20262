"""Block size is a table option, which is what the block-size experiment varies."""

import pytest

from queryengine.bootstrap import Settings, build_engine


@pytest.fixture
def engine():
    return build_engine(Settings(backend="memory", catalog_path=None))


@pytest.mark.parametrize("page_size", [1024, 2048, 4096, 8192])
def test_capacity_follows_the_declared_page_size(engine, page_size):
    engine.execute(
        f"CREATE TABLE t{page_size} (id INT PRIMARY KEY, nombre CHAR(30), monto FLOAT) "
        f"WITH (PAGE_SIZE = {page_size})"
    )
    table = next(item for item in engine.tables() if item["name"] == f"t{page_size}")
    assert table["page_size"] == page_size
    expected = (page_size - 20) // (table["stored_record_size"] + 4)
    assert table["records_per_page"] == expected


def test_a_smaller_page_needs_more_pages_for_the_same_rows(engine):
    for size in (1024, 8192):
        engine.execute(
            f"CREATE TABLE p{size} (id INT PRIMARY KEY, nombre CHAR(30)) "
            f"WITH (PAGE_SIZE = {size})"
        )
        values = ", ".join(f"({n}, 'n{n}')" for n in range(1000))
        engine.execute(f"INSERT INTO p{size} VALUES {values}")
    tables = {item["name"]: item for item in engine.tables()}
    assert tables["p1024"]["page_count"] > tables["p8192"]["page_count"]


def test_the_default_stays_four_kilobytes(engine):
    engine.execute("CREATE TABLE d (id INT PRIMARY KEY)")
    assert engine.tables()[0]["page_size"] == 4096


def test_an_unknown_option_is_rejected(engine):
    from queryengine.errors import SQLSyntaxError

    with pytest.raises(SQLSyntaxError):
        engine.execute("CREATE TABLE x (id INT) WITH (FILL_FACTOR = 80)")
