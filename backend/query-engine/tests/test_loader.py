import pytest

from queryengine.bootstrap import Settings, build_engine
from queryengine.errors import CatalogError


@pytest.fixture
def loader_engine(tmp_path):
    engine = build_engine(Settings(backend="memory", catalog_path=None, data_dir=str(tmp_path)))
    engine.execute(
        "CREATE TABLE viajes (id INT PRIMARY KEY, zona CHAR(12), monto FLOAT, alta DATE)"
    )
    return engine, tmp_path


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path.name


def test_loads_a_csv_with_a_header(loader_engine):
    engine, tmp = loader_engine
    name = write(
        tmp / "viajes.csv",
        "id,zona,monto,alta\n1,centro,10.5,2026-01-01\n2,norte,20.0,2026-01-02\n",
    )
    report = engine.load("viajes", name)
    assert report.rows_inserted == 2
    assert engine.execute("SELECT * FROM viajes WHERE id = 2").rows == [
        (2, "norte", 20.0, "2026-01-02")
    ]


def test_header_names_are_matched_loosely(loader_engine):
    engine, tmp = loader_engine
    name = write(tmp / "v.csv", "ID,Zona ,MONTO,Alta\n1,centro,10.5,2026-01-01\n")
    assert engine.load("viajes", name).rows_inserted == 1


def test_columns_the_table_does_not_have_are_ignored(loader_engine):
    engine, tmp = loader_engine
    name = write(tmp / "v.csv", "id,zona,monto,alta,extra\n1,centro,10.5,2026-01-01,basura\n")
    assert engine.load("viajes", name).rows_inserted == 1


def test_a_file_without_a_header_maps_by_position(loader_engine):
    engine, tmp = loader_engine
    name = write(tmp / "v.csv", "1,centro,10.5,2026-01-01\n")
    assert engine.load("viajes", name, header=False).rows_inserted == 1


def test_a_custom_delimiter(loader_engine):
    engine, tmp = loader_engine
    name = write(tmp / "v.csv", "id;zona;monto;alta\n1;centro;10.5;2026-01-01\n")
    assert engine.load("viajes", name, delimiter=";").rows_inserted == 1


def test_the_null_token_becomes_null(loader_engine):
    engine, tmp = loader_engine
    name = write(tmp / "v.csv", "id,zona,monto,alta\n1,NA,10.5,2026-01-01\n")
    engine.load("viajes", name, null_token="NA")
    assert engine.execute("SELECT zona FROM viajes").rows == [(None,)]


def test_bad_rows_are_rejected_not_fatal(loader_engine):
    engine, tmp = loader_engine
    name = write(
        tmp / "v.csv",
        "id,zona,monto,alta\n1,centro,10.5,2026-01-01\nxx,norte,nope,2026-01-02\n",
    )
    report = engine.load("viajes", name)
    assert report.rows_inserted == 1
    assert report.rows_rejected == 1
    assert "linea 3" in report.rejects[0]


def test_a_file_that_is_all_rejects_stops_the_load(loader_engine):
    engine, tmp = loader_engine
    rows = "\n".join(f"x{n},zona,nope,mal" for n in range(200))
    name = write(tmp / "v.csv", f"id,zona,monto,alta\n{rows}\n")
    with pytest.raises(CatalogError):
        engine.load("viajes", name)


def test_limit_stops_early(loader_engine):
    engine, tmp = loader_engine
    rows = "\n".join(f"{n},zona,1.0,2026-01-01" for n in range(50))
    name = write(tmp / "v.csv", f"id,zona,monto,alta\n{rows}\n")
    assert engine.load("viajes", name, limit=10).rows_inserted == 10


def test_a_missing_file_is_reported(loader_engine):
    engine, _ = loader_engine
    with pytest.raises(CatalogError):
        engine.load("viajes", "no_existe.csv")


def test_paths_cannot_escape_the_data_directory(loader_engine):
    engine, _ = loader_engine
    with pytest.raises(CatalogError):
        engine.load("viajes", "../../../etc/passwd")


def test_loading_updates_the_statistics_the_planner_uses(loader_engine):
    engine, tmp = loader_engine
    rows = "\n".join(f"{n},zona-{n % 4},{n * 1.5},2026-01-01" for n in range(500))
    name = write(tmp / "v.csv", f"id,zona,monto,alta\n{rows}\n")
    engine.load("viajes", name)
    table = engine.tables()[0]
    assert table["row_count"] == 500
    assert table["page_count"] > 1


def test_indexes_are_maintained_during_a_load(loader_engine):
    """The load feeds both the index and the statistics the planner reads.

    The table is large enough that the index is actually the cheaper path; on a
    handful of pages the planner would rightly prefer a scan and the assertion
    would say nothing about index maintenance.
    """
    engine, tmp = loader_engine
    engine.execute("CREATE INDEX idx_viajes ON viajes(id) USING BTREE")
    rows = "\n".join(f"{n},zona,1.0,2026-01-01" for n in range(3000))
    name = write(tmp / "v.csv", f"id,zona,monto,alta\n{rows}\n")
    engine.load("viajes", name)
    result = engine.execute("SELECT id FROM viajes WHERE id = 2500")
    assert result.rows == [(2500,)]
    assert "IndexScan" in result.plan_text


def test_copy_runs_the_loader_from_sql(loader_engine):
    engine, tmp = loader_engine
    name = write(tmp / "v.csv", "id,zona,monto,alta\n1,centro,10.5,2026-01-01\n")
    result = engine.execute(f"COPY viajes FROM '{name}'")
    assert result.affected_rows == 1
    assert "cargada" in result.message


def test_copy_accepts_options(loader_engine):
    engine, tmp = loader_engine
    name = write(tmp / "v.csv", "1;centro;10.5;2026-01-01\n")
    result = engine.execute(f"COPY viajes FROM '{name}' WITH (HEADER FALSE, DELIMITER ';')")
    assert result.affected_rows == 1


def test_copy_reports_its_cost(loader_engine):
    engine, tmp = loader_engine
    rows = "\n".join(f"{n},zona,1.0,2026-01-01" for n in range(200))
    name = write(tmp / "v.csv", f"id,zona,monto,alta\n{rows}\n")
    result = engine.execute(f"COPY viajes FROM '{name}'")
    assert result.metrics.disk_writes > 0
