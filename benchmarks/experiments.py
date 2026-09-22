"""The four experiments the technical report requires.

Each one is driven through the query engine, so what it measures is what the
engine actually does: the DiskCounter the storage layer maintains, and wall
clock time around the statement.

    python benchmarks/experiments.py --backend disk --out benchmarks/results
    python benchmarks/experiments.py --quick        # smaller N, for a smoke run

Results are written as one CSV per experiment plus a Markdown summary ready to
paste into the report.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import shutil
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend", "query-engine"))

from queryengine.bootstrap import Settings, build_engine  # noqa: E402
from queryengine.errors import QueryEngineError  # noqa: E402

FULL_SIZES = [1_000, 10_000, 50_000, 100_000, 250_000, 500_000]
QUICK_SIZES = [1_000, 5_000, 20_000]
SELECTIVITIES = [0.001, 0.01, 0.05, 0.10, 0.25]
PAGE_SIZES = [1024, 2048, 4096, 8192]
POINT_QUERIES = 1_000
SEED = 2026

# Esquema recortado del dataset de customers de Datablist: se toman las
# columnas que los experimentos necesitan y se ignora el resto del archivo.
SCHEMA = (
    "(id INT PRIMARY KEY, customerid CHAR(15), nombre CHAR(16), "
    "pais CHAR(56), alta DATE)"
)
DATASET_DIR = os.path.join(ROOT, "data")


class Harness:
    def __init__(self, backend: str, out_dir: str):
        self.backend = backend
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.workspace = tempfile.mkdtemp(prefix="cs2042-bench-")
        self.summary: list[str] = []

    def close(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def engine(self, tag: str):
        room = os.path.join(self.workspace, tag)
        os.makedirs(room, exist_ok=True)
        return build_engine(
            Settings(
                backend=self.backend,
                catalog_path=os.path.join(room, "catalog.json"),
                data_dir=self.workspace,  # the generated CSVs live here, shared across runs
                table_dir=os.path.join(room, "tables"),
            )
        )

    def dataset(self, rows: int) -> str:
        """A CSV of `rows` records, cut from the customers dataset.

        The real file is used when it is in ``data/``; otherwise a synthetic one
        of the same shape is generated, so the harness runs anywhere.
        """
        path = os.path.join(self.workspace, f"data_{rows}.csv")
        if os.path.exists(path):
            return path
        source = self._source_file(rows)
        if source is not None:
            self._cut(source, path, rows)
        else:
            self._synthesise(path, rows)
        return path

    @staticmethod
    def _source_file(rows: int) -> str | None:
        for size in (100_000, 500_000, 1_000_000, 2_000_000):
            if size < rows:
                continue
            candidate = os.path.join(DATASET_DIR, f"customers-{size}.csv")
            if os.path.exists(candidate):
                return candidate
        return None

    @staticmethod
    def _cut(source: str, target: str, rows: int) -> None:
        """Take the first `rows` records, keeping only the columns in SCHEMA.

        Source columns: Index, Customer Id, First Name, Last Name, Company,
        City, Country, Phone 1, Phone 2, Email, Subscription Date, Website.
        """
        with open(source, newline="", encoding="utf-8") as src:
            reader = csv.reader(src)
            next(reader)
            with open(target, "w", newline="", encoding="utf-8") as dst:
                writer = csv.writer(dst)
                writer.writerow(["id", "customerid", "nombre", "pais", "alta"])
                for count, row in enumerate(reader):
                    if count >= rows:
                        break
                    writer.writerow([row[0], row[1], row[2], row[6], row[10]])

    @staticmethod
    def _synthesise(target: str, rows: int) -> None:
        rng = random.Random(SEED)
        countries = ["Peru", "Chile", "Eritrea", "Botswana", "Japan", "Malta"]
        with open(target, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "customerid", "nombre", "pais", "alta"])
            for number in range(1, rows + 1):
                writer.writerow(
                    [
                        number,
                        f"cust{number:011d}",
                        f"nombre{number % 997}",
                        rng.choice(countries),
                        "2021-05-14",
                    ]
                )

    def write_csv(self, name: str, header: list[str], rows: list[list]) -> str:
        path = os.path.join(self.out_dir, name)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def section(self, title: str, header: list[str], rows: list[list]) -> None:
        self.summary.append(f"\n### {title}\n")
        self.summary.append("| " + " | ".join(header) + " |")
        self.summary.append("|" + "|".join("---" for _ in header) + "|")
        for row in rows:
            self.summary.append("| " + " | ".join(str(value) for value in row) + " |")


# -- experiment 1 -------------------------------------------------------


def insertion_cost(harness: Harness, sizes: list[int]) -> None:
    header = ["N", "estructura", "segundos", "disk_writes", "disk_reads", "filas_por_segundo"]
    rows: list[list] = []
    variants = [
        ("Heap", "HEAP", None),
        ("Sequential", "SEQUENTIAL", None),
        ("Heap + B+", "HEAP", "BTREE"),
        ("Heap + Hash", "HEAP", "HASH"),
    ]
    for size in sizes:
        source = harness.dataset(size)
        for label, storage, index in variants:
            engine = harness.engine(f"e1_{size}_{label}")
            engine.execute(f"CREATE TABLE t {SCHEMA} USING {storage}")
            if index:
                engine.execute(f"CREATE INDEX ix ON t(id) USING {index}")
            started = time.perf_counter()
            report = engine.load("t", os.path.basename(source))
            writes, reads = report.disk_writes, report.disk_reads
            if storage == "SEQUENTIAL":
                folded = engine.reorganize("t")["metrics"]
                writes += folded["disk_writes"]
                reads += folded["disk_reads"]
            elapsed = time.perf_counter() - started
            report.disk_writes, report.disk_reads = writes, reads
            rows.append(
                [
                    size,
                    label,
                    round(elapsed, 4),
                    report.disk_writes,
                    report.disk_reads,
                    round(report.rows_inserted / elapsed) if elapsed else 0,
                ]
            )
            print(f"  E1 N={size:>7} {label:<12} {elapsed:6.2f}s  {report.disk_writes:>8} writes")
    harness.write_csv("experimento1_insercion.csv", header, rows)
    harness.section("Experimento 1 — Costo de insercion masiva", header, rows)


# -- experiment 2 -------------------------------------------------------


def point_lookups(harness: Harness, size: int, queries: int) -> None:
    header = ["ruta", "lecturas_media", "lecturas_desv", "ms_media", "ms_desv"]
    rows: list[list] = []
    source = harness.dataset(size)
    rng = random.Random(SEED)
    keys = [rng.randrange(size) for _ in range(queries)]

    paths = [
        ("Full Scan (Heap)", "HEAP", None),
        ("Busqueda binaria (Sequential)", "SEQUENTIAL", None),
        ("B+ Tree", "HEAP", "BTREE"),
        ("Hash", "HEAP", "HASH"),
    ]
    for label, storage, index in paths:
        engine = harness.engine(f"e2_{label}")
        engine.execute(f"CREATE TABLE t {SCHEMA} USING {storage}")
        engine.load("t", os.path.basename(source))
        if storage == "SEQUENTIAL":
            engine.reorganize("t")
        if index:
            engine.execute(f"CREATE INDEX ix ON t(id) USING {index}")
        reads, latencies = [], []
        for key in keys:
            result = engine.execute(f"SELECT * FROM t WHERE id = {key}")
            reads.append(result.metrics.disk_reads)
            latencies.append(result.metrics.total_ms)
        rows.append(
            [
                label,
                round(statistics.fmean(reads), 2),
                round(statistics.pstdev(reads), 2),
                round(statistics.fmean(latencies), 4),
                round(statistics.pstdev(latencies), 4),
            ]
        )
        print(f"  E2 {label:<18} {statistics.fmean(reads):8.2f} lecturas de media")
    harness.write_csv("experimento2_igualdad.csv", header, rows)
    harness.section(
        f"Experimento 2 — Busquedas puntuales de igualdad (N={size}, {queries} consultas)",
        header,
        rows,
    )


# -- experiment 3 -------------------------------------------------------


def range_selectivity(harness: Harness, size: int) -> None:
    header = ["selectividad", "filas", "estructura", "ruta_elegida", "lecturas", "ms"]
    rows: list[list] = []
    source = harness.dataset(size)

    engines = {}
    for label, storage, index in [
        ("Arbol B+", "HEAP", "BTREE"),
        ("Sequential", "SEQUENTIAL", None),
        ("Full Scan", "HEAP", None),
    ]:
        engine = harness.engine(f"e3_{label}")
        engine.execute(f"CREATE TABLE t {SCHEMA} USING {storage}")
        engine.load("t", os.path.basename(source))
        if storage == "SEQUENTIAL":
            engine.reorganize("t")
        if index:
            engine.execute(f"CREATE INDEX ix ON t(id) USING {index}")
        engines[label] = engine

    for fraction in SELECTIVITIES:
        span = max(1, int(size * fraction))
        low = max(1, (size - span) // 2)
        query = f"SELECT id FROM t WHERE id >= {low} AND id <= {low + span - 1}"
        for label, engine in engines.items():
            result = engine.execute(query)
            node = (
                result.plan_text.strip().splitlines()[-1].split("(")[0].replace("->", "").strip()
            )
            rows.append(
                [
                    f"{fraction:.1%}",
                    result.row_count,
                    label,
                    node,
                    result.metrics.disk_reads,
                    round(result.metrics.total_ms, 3),
                ]
            )
            print(
                f"  E3 {fraction:>6.1%} {label:<11} {node:<20} "
                f"{result.metrics.disk_reads:>7} lecturas"
            )
    harness.write_csv("experimento3_rangos.csv", header, rows)
    harness.section(
        f"Experimento 3 — Rangos con selectividad variable (N={size})", header, rows
    )


# -- experiment 4 -------------------------------------------------------


def block_size(harness: Harness, size: int) -> None:
    header = ["page_size", "registros_por_pagina", "paginas", "lecturas_full_scan"]
    rows: list[list] = []
    source = harness.dataset(size)
    skipped = None

    for page_size in PAGE_SIZES:
        engine = harness.engine(f"e4_{page_size}")
        try:
            engine.execute(f"CREATE TABLE t {SCHEMA} WITH (PAGE_SIZE = {page_size})")
            engine.load("t", os.path.basename(source))
            table = engine.tables()[0]
            result = engine.execute(f"SELECT * FROM t WHERE id = {size - 1}")
            rows.append(
                [
                    page_size,
                    table["records_per_page"],
                    table["page_count"],
                    result.metrics.disk_reads,
                ]
            )
            print(f"  E4 B={page_size:>5}  {table['records_per_page']:>5} reg/pag")
        except QueryEngineError as exc:
            skipped = str(exc)
            print(f"  E4 B={page_size:>5}  omitido: {exc}")
    harness.write_csv("experimento4_tamano_bloque.csv", header, rows)
    harness.section(f"Experimento 4 — Sensibilidad al tamano de bloque (N={size})", header, rows)
    if skipped:
        harness.summary.append(
            f"\n> Algunos tamanos se omitieron: {skipped}\n"
        )


# -- entry point --------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Experimentos del informe CS2042")
    parser.add_argument("--backend", default="memory", choices=["memory", "disk"])
    parser.add_argument("--out", default=os.path.join(ROOT, "benchmarks", "results"))
    parser.add_argument(
        "--quick", action="store_true", help="N mas pequeno, para una corrida rapida"
    )
    parser.add_argument("--only", type=int, choices=[1, 2, 3, 4], action="append")
    args = parser.parse_args(argv)

    sizes = QUICK_SIZES if args.quick else FULL_SIZES
    point_size = sizes[-1] if args.quick else 100_000
    queries = 100 if args.quick else POINT_QUERIES
    wanted = set(args.only or [1, 2, 3, 4])

    harness = Harness(args.backend, args.out)
    print(f"backend={args.backend}  salida={args.out}")
    started = time.perf_counter()
    try:
        if 1 in wanted:
            insertion_cost(harness, sizes)
        if 2 in wanted:
            point_lookups(harness, point_size, queries)
        if 3 in wanted:
            range_selectivity(harness, point_size)
        if 4 in wanted:
            block_size(harness, min(point_size, 50_000))
    finally:
        harness.close()

    elapsed = time.perf_counter() - started
    report = os.path.join(args.out, "RESUMEN.md")
    with open(report, "w", encoding="utf-8") as handle:
        handle.write(f"# Resultados experimentales\n\nBackend: `{args.backend}`\n")
        handle.write("\n".join(harness.summary))
        handle.write("\n")
    print(f"\nlisto en {elapsed:.1f}s -> {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
