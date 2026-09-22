import pytest

from queryengine.bootstrap import Settings, build_engine

EMPLOYEES = [
    (101, "Ada Lovelace", "Analytics", 5200.0),
    (102, "Grace Hopper", "Compilers", 6100.0),
    (103, "Barbara Liskov", "Analytics", 7300.0),
    (104, "Edgar Codd", "Databases", 8200.0),
    (105, "Jim Gray", "Databases", 9100.0),
]


@pytest.fixture
def engine():
    return build_engine(Settings(backend="memory", catalog_path=None))


@pytest.fixture
def seeded(engine):
    engine.execute(
        "CREATE TABLE empleados ("
        "  id INT PRIMARY KEY,"
        "  nombre CHAR(30),"
        "  dept CHAR(20),"
        "  salario FLOAT"
        ") USING HEAP;"
    )
    values = ", ".join(
        f"({emp_id}, '{name}', '{dept}', {salary})" for emp_id, name, dept, salary in EMPLOYEES
    )
    engine.execute(f"INSERT INTO empleados VALUES {values};")
    return engine


@pytest.fixture
def seeded_sequential(engine):
    engine.execute(
        "CREATE TABLE ordenado ("
        "  id INT PRIMARY KEY,"
        "  nombre CHAR(30),"
        "  dept CHAR(20),"
        "  salario FLOAT"
        ") USING SEQUENTIAL;"
    )
    values = ", ".join(
        f"({emp_id}, '{name}', '{dept}', {salary})" for emp_id, name, dept, salary in EMPLOYEES
    )
    engine.execute(f"INSERT INTO ordenado VALUES {values};")
    return engine


def _bulk_insert(engine, table, rows, chunk=500):
    for start in range(0, len(rows), chunk):
        values = ", ".join(rows[start : start + chunk])
        engine.execute(f"INSERT INTO {table} VALUES {values};")


@pytest.fixture
def large(engine):
    """A table big enough that an index can actually beat a full scan.

    On a one-page table a sequential scan always wins, so the access path rules
    can only be observed once the table spans many pages.
    """
    engine.execute(
        "CREATE TABLE ventas (id INT PRIMARY KEY, region CHAR(12), monto FLOAT) USING HEAP;"
    )
    rows = [f"({i}, 'region-{i % 8}', {i * 1.5})" for i in range(4000)]
    _bulk_insert(engine, "ventas", rows)
    return engine


@pytest.fixture
def large_sequential(engine):
    engine.execute(
        "CREATE TABLE ventas_ord (id INT PRIMARY KEY, region CHAR(12), monto FLOAT) "
        "USING SEQUENTIAL;"
    )
    rows = [f"({i}, 'region-{i % 8}', {i * 1.5})" for i in range(4000)]
    _bulk_insert(engine, "ventas_ord", rows)
    return engine
