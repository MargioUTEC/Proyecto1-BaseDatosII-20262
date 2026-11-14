# Experimento 6: índice invertido en disco vs. Full Text Scan vs. GIN (PostgreSQL).
#   A) tiempo de construcción y tamaño en disco para N = 1 000 .. 25 000
#   B) consultas AND / OR / AND-NOT / Top-K de 1, 3 y 5 términos en el motor
#   C) las mismas consultas en PostgreSQL con to_tsvector + GIN (si se pasa --pg-dsn)
#   python benchmarks/experimento6_textual.py [--pg-dsn postgresql://usuario:clave@host:puerto/bd]

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import shutil
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RESULTS = os.path.join(ROOT, "benchmarks", "results")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend", "query-engine"))

from textual import DiskCounter, InvertedIndex, get_analyzer  # noqa: E402

SIZES = (1_000, 2_000, 5_000, 10_000, 20_000, 25_000)
LENGTHS = (1, 3, 5)
OPERATIONS = ("AND", "OR", "AND-NOT", "TOP-K")
SEED = 2026
TOP_K = 10
# palabras tal como aparecen en el texto: frecuentes, intermedias y raras
COMUNES = ["acceso", "credencial", "cámara", "rostro", "sede", "registro", "ingreso",
           "fotografía", "turno", "seguridad"]
MEDIAS = ["verificación", "biométrica", "visitante", "auditoría", "mantenimiento", "alarma",
          "permisos", "servidor", "consentimiento", "bitácora", "falla", "iluminación",
          "mascarilla", "técnico", "grabaciones", "duplicado", "horario", "recepción"]
RARAS = ["mochila", "laptop", "paraguas", "extintor", "proyector", "sospechoso", "extraviado",
         "andamio", "termo", "candado"]
TABLE_SQL = ("CREATE TABLE expedientes (id INT PRIMARY KEY, persona_id INT, tipo CHAR(32), "
             "sede CHAR(24), fecha DATE, cuerpo VARCHAR(1000)) USING HEAP;")


def read_corpus(path: str, limit: int) -> list[tuple[int, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = []
        for row in reader:
            rows.append((int(row["id"]), row["cuerpo"]))
            if len(rows) == limit:
                break
    return rows


# -- Parte A ------------------------------------------------------------------


def construction(corpus: list[tuple[int, str]], sizes, workdir: str) -> list[dict]:
    out = []
    analyzer = get_analyzer("spanish")
    for n in sizes:
        if n > len(corpus):
            print(f"  [A] se omite N={n}: el corpus tiene {len(corpus)} documentos")
            continue
        directory = os.path.join(workdir, f"build_{n}")
        counter = DiskCounter()
        started = time.perf_counter()
        index = InvertedIndex.build(directory, corpus[:n], analyzer, counter)
        elapsed = (time.perf_counter() - started) * 1000
        files = index.files()
        report = index.last_build
        row = {
            "n": n, "build_ms": round(elapsed, 2), "terms": report.terms,
            "postings": report.postings, "spimi_blocks": report.spimi_blocks,
            "disk_writes": report.disk_writes, "disk_reads": report.disk_reads,
            "vocab_bytes": files["vocab.dic"]["bytes"],
            "postings_bytes": files["postings.inv"]["bytes"],
            "docs_bytes": files["docs.bin"]["bytes"],
        }
        row["index_bytes"] = row["vocab_bytes"] + row["postings_bytes"]
        row["total_bytes"] = row["index_bytes"] + row["docs_bytes"]
        index.close()
        shutil.rmtree(directory, ignore_errors=True)
        out.append(row)
        print(f"  [A] N={n:>6}: {elapsed / 1000:6.2f} s, {report.terms} términos, "
              f"{row['index_bytes'] / 1024:,.0f} KiB (vocab+postings)")
    return out


# -- consultas ----------------------------------------------------------------


def make_queries(rng: random.Random, per_cell: int) -> list[dict]:
    pool = COMUNES * 3 + MEDIAS * 2 + RARAS
    queries = []
    for length in LENGTHS:
        for operation in OPERATIONS:
            for _ in range(per_cell):
                words: list[str] = []
                while len(words) < length:
                    word = rng.choice(pool)
                    if word not in words:
                        words.append(word)
                queries.append({"op": operation, "len": length, "words": words,
                                "engine": engine_query(operation, words),
                                "pg": pg_query(operation, words)})
    return queries


def engine_query(operation: str, words: list[str]) -> str:
    if operation == "AND":
        return " AND ".join(words)
    if operation == "OR":
        return " OR ".join(words)
    if operation == "AND-NOT":
        if len(words) == 1:
            return f"NOT {words[0]}"
        return " AND ".join(words[:-1]) + f" AND NOT {words[-1]}"
    return " ".join(words)  # TOP-K: texto libre


def pg_query(operation: str, words: list[str]) -> str:
    if operation == "AND":
        return " & ".join(words)
    if operation in ("OR", "TOP-K"):
        return " | ".join(words)
    if len(words) == 1:
        return f"!{words[0]}"
    return " & ".join(words[:-1]) + f" & !{words[-1]}"


def engine_sql(item: dict, table: str = "expedientes") -> str:
    text = item["engine"].replace("'", "''")
    if item["op"] == "TOP-K":
        return (f"SELECT id, COSINE_SCORE() AS rel FROM {table} WHERE cuerpo MATCH '{text}' "
                f"ORDER BY rel DESC LIMIT {TOP_K};")
    return f"SELECT id FROM {table} WHERE cuerpo MATCH '{text}';"


def _text_node(plan: dict) -> dict:
    stack = [plan]
    while stack:
        node = stack.pop()
        if node.get("node") in ("InvertedIndexScan", "FullTextScan"):
            return node
        stack.extend(node.get("children", []))
    raise RuntimeError("el plan no tiene un nodo de búsqueda textual")


# -- Parte B ------------------------------------------------------------------


def engine_queries(csv_path: str, n: int, queries: list[dict], workdir: str) -> list[dict]:
    from queryengine.bootstrap import Settings, build_engine

    settings = Settings(backend="disk", catalog_path=os.path.join(workdir, "catalog.json"),
                        data_dir=os.path.dirname(csv_path),
                        table_dir=os.path.join(workdir, "tables"))
    engine = build_engine(settings)
    engine.execute(TABLE_SQL)
    report = engine.load("expedientes", os.path.basename(csv_path), limit=n)
    pages = engine.tables()[0]["page_count"]
    print(f"  [B] tabla expedientes: {report.rows_inserted} filas en {pages} páginas de 4 KB")

    rows = []
    for method in ("FullTextScan", "InvertedIndexScan"):
        if method == "InvertedIndexScan":
            started = time.perf_counter()
            created = engine.execute("CREATE INDEX ix_cuerpo ON expedientes(cuerpo) USING INVERTED "
                                     "WITH (scoring = 'TFIDF_COSINE', language = 'spanish');")
            print(f"  [B] CREATE INDEX: {(time.perf_counter() - started):.2f} s "
                  f"({created.metrics.disk_reads} lecturas, {created.metrics.disk_writes} escrituras)")
        for item in queries:
            result = engine.execute(engine_sql(item))
            node = _text_node(result.plan)
            assert node["node"] == method, (node["node"], method)
            runtime = node["runtime"]
            rows.append({
                "system": "motor", "method": method, "op": item["op"], "len": item["len"],
                "query": item["engine"], "rows": result.row_count,
                "index_reads": runtime["index_reads"], "table_reads": runtime["table_reads"],
                "total_reads": result.metrics.disk_reads,
                "ms": result.metrics.total_ms, "block_bytes": 4096,
            })
        print(f"  [B] {method}: {len(queries)} consultas")
    return rows


# -- Parte C ------------------------------------------------------------------


def postgres(dsn: str, csv_path: str, n: int, queries: list[dict], sizes) -> tuple[list, list]:
    try:
        import psycopg
    except ImportError:
        print("  [C] psycopg no está instalado; se omite PostgreSQL")
        return [], []
    try:
        conn = psycopg.connect(dsn, autocommit=True, connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de conexión omite la parte C
        print(f"  [C] no se pudo conectar a PostgreSQL ({exc}); se omite")
        return [], []
    build_rows, query_rows = [], []
    with conn, conn.cursor() as cur:
        corpus = read_corpus(csv_path, max(max(sizes), n))
        for size in [s for s in sizes if s <= len(corpus)]:
            cur.execute("DROP TABLE IF EXISTS exp6_gin")
            cur.execute("CREATE TABLE exp6_gin (id INT PRIMARY KEY, cuerpo TEXT, tsv tsvector)")
            with cur.copy("COPY exp6_gin (id, cuerpo) FROM STDIN") as copy:
                for row in corpus[:size]:
                    copy.write_row(row)
            started = time.perf_counter()
            cur.execute("UPDATE exp6_gin SET tsv = to_tsvector('spanish', cuerpo)")
            cur.execute("CREATE INDEX exp6_gin_ix ON exp6_gin USING GIN (tsv)")
            elapsed = (time.perf_counter() - started) * 1000
            cur.execute("SELECT pg_relation_size('exp6_gin_ix')")
            build_rows.append({"n": size, "gin_build_ms": round(elapsed, 2),
                               "gin_bytes": cur.fetchone()[0]})
            print(f"  [C] GIN N={size}: {elapsed / 1000:.2f} s, {build_rows[-1]['gin_bytes']:,} B")
        cur.execute("ANALYZE exp6_gin")
        cur.execute("DROP TABLE IF EXISTS exp6_gin_n")
        cur.execute(f"CREATE TABLE exp6_gin_n AS SELECT * FROM exp6_gin WHERE id <= {n}")
        cur.execute("CREATE INDEX exp6_gin_n_ix ON exp6_gin_n USING GIN (tsv)")
        cur.execute("ANALYZE exp6_gin_n")
        for item in queries:
            q = item["pg"]
            if item["op"] == "TOP-K":
                sql = ("SELECT id, ts_rank(tsv, query) AS rel FROM exp6_gin_n, "
                       "to_tsquery('spanish', %s) query WHERE tsv @@ query "
                       f"ORDER BY rel DESC LIMIT {TOP_K}")
            else:
                sql = "SELECT id FROM exp6_gin_n WHERE tsv @@ to_tsquery('spanish', %s)"
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, (q,))
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, (q,))  # caché caliente
            raw = cur.fetchone()[0]
            plan = (json.loads(raw) if isinstance(raw, str) else raw)[0]
            top = plan["Plan"]
            blocks = top.get("Shared Hit Blocks", 0) + top.get("Shared Read Blocks", 0)
            query_rows.append({
                "system": "postgres", "method": "GIN", "op": item["op"], "len": item["len"],
                "query": q, "rows": top.get("Actual Rows", 0), "index_reads": "",
                "table_reads": "", "total_reads": blocks,
                "ms": round(plan["Execution Time"], 4), "block_bytes": 8192,
            })
        cur.execute("DROP TABLE IF EXISTS exp6_gin, exp6_gin_n")
    print(f"  [C] GIN: {len(query_rows)} consultas")
    return build_rows, query_rows


# -- salida -------------------------------------------------------------------


def write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        groups.setdefault((row["method"], row["op"], row["len"]), []).append(row)
    out = []
    for (method, op, length), items in groups.items():
        ms = [r["ms"] for r in items]
        out.append({
            "method": method, "op": op, "len": length, "queries": len(items),
            "avg_rows": round(statistics.mean(r["rows"] for r in items), 1),
            "avg_index_reads": _avg(items, "index_reads"),
            "avg_table_reads": _avg(items, "table_reads"),
            "avg_total_reads": _avg(items, "total_reads"),
            "avg_ms": round(statistics.mean(ms), 3),
            "stdev_ms": round(statistics.stdev(ms), 3) if len(ms) > 1 else 0.0,
        })
    order = {"FullTextScan": 0, "InvertedIndexScan": 1, "GIN": 2}
    return sorted(out, key=lambda r: (OPERATIONS.index(r["op"]), r["len"], order[r["method"]]))


def _avg(items, key):
    values = [r[key] for r in items if r[key] != ""]
    return round(statistics.mean(values), 1) if values else ""


def plots(build: list[dict], gin_build: list[dict], summary: list[dict], n_query: int) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib no está instalado; se omiten las gráficas")
        return
    colors = {"FullTextScan": "#c2410c", "InvertedIndexScan": "#2563eb", "GIN": "#16a34a"}

    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4))
    ns = [r["n"] for r in build]
    left.plot(ns, [r["build_ms"] / 1000 for r in build], "o-", color=colors["InvertedIndexScan"],
              label="SPIMI (motor)")
    right.plot(ns, [r["index_bytes"] / 2**20 for r in build], "o-",
               color=colors["InvertedIndexScan"], label="vocab.dic + postings.inv")
    right.plot(ns, [r["total_bytes"] / 2**20 for r in build], "o--", color="#64748b",
               label="+ docs.bin (normas)")
    if gin_build:
        left.plot([r["n"] for r in gin_build], [r["gin_build_ms"] / 1000 for r in gin_build],
                  "s-", color=colors["GIN"], label="GIN (PostgreSQL)")
        right.plot([r["n"] for r in gin_build], [r["gin_bytes"] / 2**20 for r in gin_build],
                   "s-", color=colors["GIN"], label="índice GIN")
    left.set(title="Tiempo de construcción", xlabel="documentos (N)", ylabel="segundos")
    right.set(title="Tamaño en disco", xlabel="documentos (N)", ylabel="MiB")
    for axis in (left, right):
        axis.grid(alpha=0.3)
        axis.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS, "experimento6_construccion.svg"))
    plt.close(fig)

    for metric, label, filename in (("avg_ms", "latencia media (ms, escala log)", "latencia"),
                                    ("avg_total_reads", "bloques leídos (escala log)", "io")):
        fig, axes = plt.subplots(1, len(OPERATIONS), figsize=(14, 4), sharey=True)
        for axis, op in zip(axes, OPERATIONS, strict=True):
            methods = [m for m in colors if any(r["method"] == m and r["op"] == op for r in summary)]
            width = 0.8 / max(1, len(methods))
            for i, method in enumerate(methods):
                values = [next((r[metric] for r in summary if r["method"] == method
                                and r["op"] == op and r["len"] == length), 0) or 0
                          for length in LENGTHS]
                axis.bar([x + i * width for x in range(len(LENGTHS))], values, width,
                         color=colors[method], label=method)
            axis.set_xticks([x + width * (len(methods) - 1) / 2 for x in range(len(LENGTHS))],
                            [f"{n} térm." for n in LENGTHS])
            axis.set_yscale("log")
            axis.set_title(op)
            axis.grid(axis="y", alpha=0.3)
        axes[0].set_ylabel(label)
        axes[-1].legend(fontsize=8)
        fig.suptitle(f"Consultas sobre N = {n_query:,} expedientes")
        fig.tight_layout()
        fig.savefig(os.path.join(RESULTS, f"experimento6_{filename}.svg"))
        plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Experimento 6: índice invertido vs. Full Text Scan vs. GIN")
    parser.add_argument("--corpus", default=os.path.join(DATA, "expedientes.csv"))
    parser.add_argument("--sizes", type=int, nargs="+", default=list(SIZES))
    parser.add_argument("--n-consultas", type=int, default=20_000,
                        help="tamaño de la colección para la parte B/C")
    parser.add_argument("--por-celda", type=int, default=5,
                        help="consultas distintas por (operación, n.º de términos)")
    parser.add_argument("--pg-dsn", default=os.getenv("PG_DSN"))
    args = parser.parse_args()

    if not os.path.exists(args.corpus):
        raise SystemExit(f"no existe {args.corpus}: corre scripts/generar_expedientes.py")
    os.makedirs(RESULTS, exist_ok=True)
    corpus = read_corpus(args.corpus, max(max(args.sizes), args.n_consultas))
    queries = make_queries(random.Random(SEED), args.por_celda)

    workdir = tempfile.mkdtemp(prefix="exp6_")
    try:
        print("Parte A: construcción del índice invertido")
        build = construction(corpus, args.sizes, workdir)
        print(f"Parte B: consultas sobre el motor (N = {args.n_consultas})")
        engine_rows = engine_queries(args.corpus, args.n_consultas, queries, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    gin_build, gin_rows = [], []
    if args.pg_dsn:
        print("Parte C: PostgreSQL GIN")
        gin_build, gin_rows = postgres(args.pg_dsn, args.corpus, args.n_consultas, queries,
                                       args.sizes)
    else:
        print("Parte C omitida: pasa --pg-dsn o define PG_DSN para comparar con GIN")

    if gin_build:
        by_n = {r["n"]: r for r in gin_build}
        for row in build:
            row.update({k: v for k, v in by_n.get(row["n"], {}).items() if k != "n"})
    summary = summarize(engine_rows + gin_rows)
    write_csv(os.path.join(RESULTS, "experimento6_construccion.csv"), build)
    write_csv(os.path.join(RESULTS, "experimento6_consultas.csv"), engine_rows + gin_rows)
    write_csv(os.path.join(RESULTS, "experimento6_resumen.csv"), summary)
    plots(build, gin_build, summary, args.n_consultas)
    print("\nResumen (promedios por celda):")
    print(f"{'op':8} {'térm':>4} {'método':18} {'filas':>8} {'lect. índice':>12} "
          f"{'lect. tabla':>11} {'lect. total':>11} {'ms':>9}")
    for r in summary:
        print(f"{r['op']:8} {r['len']:>4} {r['method']:18} {r['avg_rows']:>8} "
              f"{r['avg_index_reads']!s:>12} {r['avg_table_reads']!s:>11} "
              f"{r['avg_total_reads']!s:>11} {r['avg_ms']:>9}")
    print(f"\nResultados en {RESULTS}")


if __name__ == "__main__":
    main()
