import pytest

from queryengine.errors import CatalogError, TypeMismatchError


def rows_of(engine, sql):
    return engine.execute(sql).rows


def test_select_star_returns_every_column(seeded):
    result = seeded.execute("SELECT * FROM empleados")
    assert result.columns == ("id", "nombre", "dept", "salario")
    assert result.row_count == 5


def test_projection_reshapes_the_rows(seeded):
    result = seeded.execute("SELECT nombre, id FROM empleados WHERE id = 101")
    assert result.columns == ("nombre", "id")
    assert result.rows == [("Ada Lovelace", 101)]


def test_equality_filter(seeded):
    assert rows_of(seeded, "SELECT id FROM empleados WHERE dept = 'Databases'") == [(104,), (105,)]


def test_range_filter_is_inclusive(seeded):
    found = rows_of(seeded, "SELECT id FROM empleados WHERE id >= 102 AND id <= 104")
    assert found == [(102,), (103,), (104,)]


def test_strict_bounds_exclude_the_endpoints(seeded):
    found = rows_of(seeded, "SELECT id FROM empleados WHERE id > 102 AND id < 105")
    assert found == [(103,), (104,)]


def test_strict_bounds_hold_through_an_index(seeded):
    seeded.execute("CREATE INDEX idx_id ON empleados(id) USING BTREE")
    found = rows_of(seeded, "SELECT id FROM empleados WHERE id > 102 AND id < 105")
    assert found == [(103,), (104,)]


def test_index_and_scan_agree(seeded):
    without = rows_of(seeded, "SELECT id FROM empleados WHERE id >= 102 AND id <= 104")
    seeded.execute("CREATE INDEX idx_id ON empleados(id) USING BTREE")
    assert rows_of(seeded, "SELECT id FROM empleados WHERE id >= 102 AND id <= 104") == without


def test_disjunction(seeded):
    found = rows_of(seeded, "SELECT id FROM empleados WHERE id = 101 OR id = 105")
    assert found == [(101,), (105,)]


def test_negation(seeded):
    found = rows_of(seeded, "SELECT id FROM empleados WHERE NOT dept = 'Analytics'")
    assert found == [(102,), (104,), (105,)]


def test_order_by_descending(seeded):
    found = rows_of(seeded, "SELECT id FROM empleados ORDER BY salario DESC")
    assert found == [(105,), (104,), (103,), (102,), (101,)]


def test_limit_stops_early(seeded):
    assert len(rows_of(seeded, "SELECT * FROM empleados LIMIT 2")) == 2


def test_literals_are_coerced_to_the_column_type(seeded):
    assert rows_of(seeded, "SELECT id FROM empleados WHERE id = '101'") == [(101,)]


def test_uncoercible_literal_is_rejected(seeded):
    with pytest.raises(TypeMismatchError):
        seeded.execute("SELECT * FROM empleados WHERE id = 'abc'")


def test_insert_with_named_columns_fills_the_rest_with_null(seeded):
    seeded.execute("INSERT INTO empleados (id, nombre) VALUES (106, 'Michael Stonebraker')")
    assert rows_of(seeded, "SELECT dept FROM empleados WHERE id = 106") == [(None,)]


def test_null_comparisons_are_unknown_not_false(seeded):
    seeded.execute("INSERT INTO empleados (id, nombre) VALUES (106, 'Michael Stonebraker')")
    assert rows_of(seeded, "SELECT id FROM empleados WHERE dept = 'Analytics'") == [(101,), (103,)]
    assert rows_of(seeded, "SELECT id FROM empleados WHERE dept IS NULL") == [(106,)]


def test_primary_key_rejects_null(seeded):
    with pytest.raises(CatalogError):
        seeded.execute("INSERT INTO empleados (nombre) VALUES ('sin id')")


def test_wrong_arity_is_rejected(seeded):
    with pytest.raises(CatalogError):
        seeded.execute("INSERT INTO empleados VALUES (1, 'solo dos')")


def test_delete_removes_matching_rows(seeded):
    result = seeded.execute("DELETE FROM empleados WHERE dept = 'Analytics'")
    assert result.affected_rows == 2
    assert seeded.execute("SELECT * FROM empleados").row_count == 3


def test_delete_keeps_indexes_consistent(seeded):
    seeded.execute("CREATE INDEX idx_id ON empleados(id) USING BTREE")
    seeded.execute("DELETE FROM empleados WHERE id = 101")
    assert rows_of(seeded, "SELECT id FROM empleados WHERE id = 101") == []


def test_char_values_are_truncated_at_their_declared_width(engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, code CHAR(4))")
    with pytest.raises(TypeMismatchError):
        engine.execute("INSERT INTO t VALUES (1, 'demasiado largo')")


def test_dates_are_validated(engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, alta DATE)")
    engine.execute("INSERT INTO t VALUES (1, '2026-09-19')")
    with pytest.raises(TypeMismatchError):
        engine.execute("INSERT INTO t VALUES (2, '19/09/2026')")


def test_record_layout_is_derived_from_the_schema(engine):
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, nombre CHAR(30), salario FLOAT)")
    table = next(item for item in engine.tables() if item["name"] == "t")
    assert table["record_format"] == "<i30sf"
    assert table["record_size"] == 38


def test_metrics_report_block_transfers(seeded):
    result = seeded.execute("SELECT * FROM empleados")
    assert result.metrics.disk_reads > 0
    assert result.metrics.total_ms >= 0


def test_an_index_scan_reads_fewer_blocks_than_a_full_scan(engine):
    engine.execute("CREATE TABLE grande (id INT PRIMARY KEY, valor INT)")
    values = ", ".join(f"({i}, {i * 2})" for i in range(2000))
    engine.execute(f"INSERT INTO grande VALUES {values}")

    full = engine.execute("SELECT * FROM grande WHERE id = 1500").metrics.disk_reads
    engine.execute("CREATE INDEX idx_grande ON grande(id) USING BTREE")
    indexed = engine.execute("SELECT * FROM grande WHERE id = 1500").metrics.disk_reads

    assert indexed < full


def test_reorganize_reports_what_it_moved(seeded_sequential):
    report = seeded_sequential.reorganize("ordenado")
    assert report["report"]["records_kept"] == 5
    assert report["metrics"]["disk_writes"] > 0


def test_reorganize_rejects_heap_tables(seeded):
    with pytest.raises(CatalogError):
        seeded.reorganize("empleados")


def test_dropping_a_table_drops_its_indexes(seeded):
    seeded.execute("CREATE INDEX idx_id ON empleados(id) USING BTREE")
    seeded.execute("DROP TABLE empleados")
    assert seeded.tables() == []
