"""End to end over the shape of the project's dataset.

The Customers file from Datablist has a column literally named ``Index``, which
is a SQL keyword, and others with spaces in them. Quoting lets the table mirror
the file's own names, and then COPY matches the header with no column list.
"""

import csv

import pytest

from queryengine.bootstrap import Settings, build_engine
from queryengine.errors import CatalogError

HEADER = [
    "Index", "Customer Id", "First Name", "Last Name", "Company", "City",
    "Country", "Phone 1", "Phone 2", "Email", "Subscription Date", "Website",
]

CREATE = """
CREATE TABLE customers (
  "Index" INT PRIMARY KEY, "Customer Id" CHAR(15), "First Name" CHAR(16),
  "Last Name" CHAR(16), "Company" CHAR(40), "City" CHAR(28), "Country" CHAR(56),
  "Phone 1" CHAR(24), "Phone 2" CHAR(24), "Email" CHAR(48),
  "Subscription Date" DATE, "Website" CHAR(44)
) USING %s
"""


def sample(path, rows=400):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for n in range(1, rows + 1):
            writer.writerow([
                n, f"cust{n:011d}", f"Nombre{n}", f"Apellido{n}", f"Empresa {n} SAC",
                f"Ciudad{n}", ["Peru", "Chile", "Eritrea"][n % 3],
                f"555-{n:07d}", f"(01){n:07d}", f"user{n}@ejemplo.com",
                "2021-05-14", f"https://ejemplo{n}.com/",
            ])
    return path.name


@pytest.fixture
def engine(tmp_path):
    return build_engine(
        Settings(
            backend="memory",
            catalog_path=None,
            data_dir=str(tmp_path),
            table_dir=str(tmp_path / "tables"),
        )
    )


def test_quoted_names_mirror_the_files_header(engine, tmp_path):
    name = sample(tmp_path / "customers.csv")
    engine.execute(CREATE % "HEAP")
    report = engine.load("customers", name)
    assert report.rows_inserted == 400
    assert report.rows_rejected == 0


def test_a_keyword_column_can_be_queried_when_quoted(engine, tmp_path):
    name = sample(tmp_path / "customers.csv")
    engine.execute(CREATE % "HEAP")
    engine.load("customers", name)
    result = engine.execute('SELECT "First Name", "Country" FROM customers WHERE "Index" = 42')
    assert result.rows == [("Nombre42", "Peru")]  # 42 % 3 == 0


def test_the_dataset_loads_into_a_sequential_table_too(engine, tmp_path):
    name = sample(tmp_path / "customers.csv")
    engine.execute(CREATE % "SEQUENTIAL")
    engine.load("customers", name)
    engine.reorganize("customers")
    result = engine.execute('SELECT "Email" FROM customers WHERE "Index" = 300')
    assert result.rows == [("user300@ejemplo.com",)]


def test_a_mismatched_header_says_which_columns_went_unfilled(engine, tmp_path):
    path = tmp_path / "otro.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["no_existe", "tampoco"])
        for n in range(200):
            writer.writerow([n, "x"])
    engine.execute(CREATE % "HEAP")
    with pytest.raises(CatalogError) as error:
        engine.load("customers", path.name)
    assert "Index" in str(error.value)


def test_dates_from_the_file_are_validated(engine, tmp_path):
    path = tmp_path / "customers.csv"
    sample(path, rows=5)
    rows = path.read_text(encoding="utf-8").replace("2021-05-14", "14/05/2021")
    path.write_text(rows, encoding="utf-8")
    engine.execute(CREATE % "HEAP")
    report = engine.load("customers", path.name)
    assert report.rows_inserted == 0
    assert report.rows_rejected == 5
