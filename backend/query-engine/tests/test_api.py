import pytest
from fastapi.testclient import TestClient

from queryengine.api.app import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("QE_STORAGE_BACKEND", "memory")
    monkeypatch.setenv("QE_CATALOG_PATH", str(tmp_path / "catalog.json"))
    with TestClient(create_app()) as test_client:
        yield test_client


def run(client, sql):
    return client.post("/api/query", json={"sql": sql})


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_query_returns_rows_plan_and_metrics(client):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY, nombre CHAR(20))")
    run(client, "INSERT INTO t VALUES (1, 'Ada'), (2, 'Grace')")

    body = run(client, "SELECT * FROM t WHERE id = 1").json()
    assert body["columns"] == ["id", "nombre"]
    assert body["rows"] == [[1, "Ada"]]
    assert body["plan"]["node"] in ("SeqScan", "Filter")
    assert body["metrics"]["disk_reads"] >= 0
    assert "total_ms" in body["metrics"]


def test_tables_lists_schema_and_indexes(client):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY, nombre CHAR(20)) USING SEQUENTIAL")
    run(client, "CREATE INDEX idx_t ON t(id) USING HASH")

    table = client.get("/api/tables").json()["tables"][0]
    assert table["storage"] == "SEQUENTIAL"
    assert table["columns"][0]["primary_key"] is True
    assert table["indexes"][0]["kind"] == "HASH"


def test_reorganize_endpoint(client):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY) USING SEQUENTIAL")
    run(client, "INSERT INTO t VALUES (3), (1), (2)")

    body = client.post("/api/tables/reorganize", json={"table": "t"}).json()
    assert body["report"]["records_kept"] == 3


def test_syntax_errors_come_back_as_400_with_a_position(client):
    response = run(client, "SELECT * FROM")
    assert response.status_code == 400
    body = response.json()
    assert body["kind"] == "SQLSyntaxError"
    assert body["line"] == 1


def test_unknown_table_is_a_400(client):
    response = run(client, "SELECT * FROM inexistente")
    assert response.status_code == 400
    assert response.json()["kind"] == "CatalogError"


def test_catalog_survives_a_restart(client, monkeypatch, tmp_path):
    run(client, "CREATE TABLE persistente (id INT PRIMARY KEY)")
    with TestClient(create_app()) as reopened:
        names = [table["name"] for table in reopened.get("/api/tables").json()["tables"]]
    assert "persistente" in names
