"""Experimento 5: R-Tree frente a escaneo secuencial en páginas de 4 KiB.

    python benchmarks/experimento5_espacial.py

Escribe dos CSV y tres gráficas SVG en benchmarks/results/. Los archivos
temporales de datos e índices se eliminan al terminar.
"""

from __future__ import annotations

import argparse
import csv
import heapq
import math
import os
import random
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from rtree import DiskCounter, RTree  # noqa: E402

PAGE_SIZE = 4096
PAGE_HEADER = __import__("struct").Struct("<II")
SIZES = (10_000, 50_000, 100_000)
K_VALUES = (5, 20, 50)
DIMENSIONS = (2, 4, 8, 16, 32, 64, 128)
SEED = 2026
CSV_FIELDS = (
    "scope", "operation", "n", "d", "k", "method", "queries",
    "index_reads", "table_reads", "total_reads", "avg_ms", "stdev_ms",
    "avg_results", "tree_pages", "tree_height", "build_ms",
)


class PointPages:
    """Fixed 4096-byte pages holding fixed-width float32 vectors."""

    def __init__(self, path: str, dimensions: int, rows: int):
        import struct

        self.path = path
        self.dimensions = dimensions
        self.rows = rows
        self.vector = struct.Struct(f"<{dimensions}f")
        self.capacity = (PAGE_SIZE - PAGE_HEADER.size) // self.vector.size
        self.page_count = math.ceil(rows / self.capacity)
        self.counter = DiskCounter()

    def build(self, vectors) -> None:
        page_id = 0
        count = 0
        buf = bytearray(PAGE_SIZE)
        PAGE_HEADER.pack_into(buf, 0, page_id, 0)
        with open(self.path, "w+b") as handle:
            for vector in vectors:
                offset = PAGE_HEADER.size + count * self.vector.size
                self.vector.pack_into(buf, offset, *vector)
                count += 1
                if count == self.capacity or page_id * self.capacity + count == self.rows:
                    PAGE_HEADER.pack_into(buf, 0, page_id, count)
                    handle.seek(page_id * PAGE_SIZE)
                    handle.write(buf)
                    page_id += 1
                    count = 0
                    if page_id * self.capacity < self.rows:
                        buf = bytearray(PAGE_SIZE)
                        PAGE_HEADER.pack_into(buf, 0, page_id, 0)

    def read_page(self, handle, page_id: int) -> tuple[int, list[tuple[float, ...]]]:
        handle.seek(page_id * PAGE_SIZE)
        raw = handle.read(PAGE_SIZE)
        self.counter.disk_reads += 1
        if len(raw) != PAGE_SIZE:
            raise ValueError(f"página de datos {page_id} truncada")
        stored_id, count = PAGE_HEADER.unpack_from(raw)
        if stored_id != page_id:
            raise ValueError(f"se esperaba página {page_id} y se leyó {stored_id}")
        vectors = [
            self.vector.unpack_from(raw, PAGE_HEADER.size + slot * self.vector.size)
            for slot in range(count)
        ]
        return count, vectors

    def scan(self):
        with open(self.path, "rb") as handle:
            for page_id in range(self.page_count):
                _count, vectors = self.read_page(handle, page_id)
                first_row = page_id * self.capacity
                for slot, vector in enumerate(vectors):
                    yield (page_id, slot), vector, first_row + slot

    def fetch(self, rid: tuple[int, int]) -> tuple[float, ...]:
        page_id, slot = rid
        with open(self.path, "rb") as handle:
            count, vectors = self.read_page(handle, page_id)
        if slot < 0 or slot >= count:
            raise IndexError(f"RID fuera de página: {rid}")
        return vectors[slot]

    def reset_counter(self) -> None:
        self.counter.reset()


def vector_stream(n: int, d: int, seed: int, geographic: bool = False):
    rng = random.Random(seed)
    for _ in range(n):
        if geographic:
            # Fracciones binarias: los valores son exactos en float32 y los
            # MBR almacenados coinciden con los puntos que consulta el baseline.
            yield (-80.0 + rng.randrange(0, 2560) / 256,
                   -15.0 + rng.randrange(0, 1280) / 256)
        else:
            yield tuple(rng.randrange(-256, 257) / 256 for _ in range(d))


def build_index(pages: PointPages, index_path: str, dimensions: int) -> tuple[RTree, float]:
    counter = DiskCounter()
    started = time.perf_counter()
    tree = RTree(index_path, D=dimensions, dtype="f", page_size=PAGE_SIZE, counter=counter)
    for rid, vector, _ in pages.scan():
        tree.insert_point(vector, rid)
    elapsed_ms = (time.perf_counter() - started) * 1000
    pages.reset_counter()
    counter.reset()
    return tree, elapsed_ms


def euclidean2(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=False))


def rtree_radius(tree: RTree, pages: PointPages, point, radius: float):
    tree.counter.reset()
    pages.reset_counter()
    started = time.perf_counter()
    candidates = tree.search_radius(point, radius)
    found = set()
    with open(pages.path, "rb") as handle:
        for _distance, rid, _mbr in candidates:
            count, vectors = pages.read_page(handle, rid[0])
            if 0 <= rid[1] < count and euclidean2(vectors[rid[1]], point) <= radius * radius:
                found.add(rid)
    elapsed = (time.perf_counter() - started) * 1000
    return found, tree.counter.disk_reads, pages.counter.disk_reads, elapsed


def sequential_radius(pages: PointPages, point, radius: float):
    pages.reset_counter()
    started = time.perf_counter()
    found = set()
    for rid, vector, _row in pages.scan():
        if euclidean2(vector, point) <= radius * radius:
            found.add(rid)
    elapsed = (time.perf_counter() - started) * 1000
    return found, 0, pages.counter.disk_reads, elapsed


def rtree_knn(tree: RTree, pages: PointPages, point, k: int):
    tree.counter.reset()
    pages.reset_counter()
    started = time.perf_counter()
    nearest = tree.knn(point, k)
    values = []
    with open(pages.path, "rb") as handle:
        for _index_distance, rid, _mbr in nearest:
            count, vectors = pages.read_page(handle, rid[0])
            if 0 <= rid[1] < count:
                values.append((euclidean2(vectors[rid[1]], point), rid))
    values.sort(key=lambda item: item[0])
    elapsed = (time.perf_counter() - started) * 1000
    return values, tree.counter.disk_reads, pages.counter.disk_reads, elapsed


def sequential_knn(pages: PointPages, point, k: int):
    pages.reset_counter()
    started = time.perf_counter()
    heap = []
    for rid, vector, _row in pages.scan():
        distance = euclidean2(vector, point)
        item = (-distance, -rid[0], -rid[1])
        if len(heap) < k:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    values = sorted((-item[0], (-item[1], -item[2])) for item in heap)
    elapsed = (time.perf_counter() - started) * 1000
    return values, 0, pages.counter.disk_reads, elapsed


def check_distances(indexed, sequential, tolerance=1e-10) -> None:
    a = sorted(distance for distance, _rid in indexed)
    b = sorted(distance for distance, _rid in sequential)
    if len(a) != len(b) or any(abs(x - y) > tolerance for x, y in zip(a, b, strict=True)):
        raise AssertionError("R-Tree y escaneo secuencial discrepan en distancias k-NN")


def summarize(rows: list[dict], scope: str, operation: str, n: int, d: int, k,
              method: str, index_reads: list[int], table_reads: list[int], times: list[float],
              results: list[int], tree: RTree, build_ms: float) -> None:
    total = [i + t for i, t in zip(index_reads, table_reads, strict=True)]
    rows.append({
        "scope": scope,
        "operation": operation,
        "n": n,
        "d": d,
        "k": "" if k is None else k,
        "method": method,
        "queries": len(times),
        "index_reads": round(statistics.fmean(index_reads), 3),
        "table_reads": round(statistics.fmean(table_reads), 3),
        "total_reads": round(statistics.fmean(total), 3),
        "avg_ms": round(statistics.fmean(times), 4),
        "stdev_ms": round(statistics.pstdev(times), 4),
        "avg_results": round(statistics.fmean(results), 2),
        "tree_pages": tree.num_pages,
        "tree_height": tree.height,
        "build_ms": round(build_ms, 2),
    })


def run_geographic(workspace: str, out_rows: list[dict], queries: int) -> None:
    rng = random.Random(SEED + 1)
    for n in SIZES:
        pages = PointPages(os.path.join(workspace, f"geo-{n}.bin"), 2, n)
        pages.build(vector_stream(n, 2, SEED + n, geographic=True))
        tree, build_ms = build_index(pages, os.path.join(workspace, f"geo-{n}.rtree"), 2)
        radius_data = {"R-Tree": [[], [], [], []], "Secuencial": [[], [], [], []]}
        knn_data = {
            k: {"R-Tree": [[], [], [], []], "Secuencial": [[], [], [], []]}
            for k in K_VALUES
        }
        for _ in range(queries):
            point = (-80.0 + rng.randrange(0, 2560) / 256,
                     -15.0 + rng.randrange(0, 1280) / 256)
            indexed = rtree_radius(tree, pages, point, 0.125)
            sequential = sequential_radius(pages, point, 0.125)
            if indexed[0] != sequential[0]:
                raise AssertionError(f"radio: resultados distintos para N={n}")
            radius_data["R-Tree"][0].append(indexed[1]); radius_data["R-Tree"][1].append(indexed[2])
            radius_data["R-Tree"][2].append(indexed[3]); radius_data["R-Tree"][3].append(len(indexed[0]))
            radius_data["Secuencial"][0].append(sequential[1]); radius_data["Secuencial"][1].append(sequential[2])
            radius_data["Secuencial"][2].append(sequential[3]); radius_data["Secuencial"][3].append(len(sequential[0]))
            for k in K_VALUES:
                indexed_k = rtree_knn(tree, pages, point, k)
                sequential_k = sequential_knn(pages, point, k)
                check_distances(indexed_k[0], sequential_k[0])
                for method, result in (("R-Tree", indexed_k), ("Secuencial", sequential_k)):
                    values = knn_data[k][method]
                    values[0].append(result[1]); values[1].append(result[2])
                    values[2].append(result[3]); values[3].append(len(result[0]))

        for method, data in radius_data.items():
            summarize(out_rows, "geografico_2d", "radio", n, 2, None, method,
                      data[0], data[1], data[2], data[3], tree, build_ms)
        for k, methods in knn_data.items():
            for method, data in methods.items():
                summarize(out_rows, "geografico_2d", "knn", n, 2, k, method,
                          data[0], data[1], data[2], data[3], tree, build_ms)
        tree.close()
        print(f"Geo N={n:,}: {tree.height} niveles, {tree.num_pages:,} páginas")


def run_dimensions(workspace: str, out_rows: list[dict], n: int, queries: int) -> None:
    rng = random.Random(SEED + 2)
    for d in DIMENSIONS:
        pages = PointPages(os.path.join(workspace, f"dim-{d}.bin"), d, n)
        pages.build(vector_stream(n, d, SEED + d))
        tree, build_ms = build_index(pages, os.path.join(workspace, f"dim-{d}.rtree"), d)
        data = {method: [[], [], [], []] for method in ("R-Tree", "Secuencial")}
        k = 20
        for _ in range(queries):
            point = tuple(rng.randrange(-256, 257) / 256 for _ in range(d))
            indexed = rtree_knn(tree, pages, point, k)
            sequential = sequential_knn(pages, point, k)
            check_distances(indexed[0], sequential[0])
            for method, result in (("R-Tree", indexed), ("Secuencial", sequential)):
                values = data[method]
                values[0].append(result[1]); values[1].append(result[2])
                values[2].append(result[3]); values[3].append(len(result[0]))
        for method, values in data.items():
            summarize(out_rows, "dimensionalidad", "knn", n, d, k, method,
                      values[0], values[1], values[2], values[3], tree, build_ms)
        tree.close()
        print(f"Dim D={d:>3}: {tree.height} niveles, {tree.num_pages:,} páginas")


def write_csv(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def svg_chart(path: str, title: str, labels: list[str], series: list[tuple[str, list[float], str]],
              y_label: str, log_scale: bool = False) -> None:
    width, height = 760, 430
    left, right, top, bottom = 78, 24, 58, 88
    plot_w, plot_h = width - left - right, height - top - bottom
    flat = [value for _name, values, _color in series for value in values if value >= 0]
    positive = [value for value in flat if value > 0]
    if log_scale:
        lo = math.floor(math.log10(min(positive))) if positive else -1
        hi = math.ceil(math.log10(max(positive))) if positive else 1
        if lo == hi: lo -= 1; hi += 1
        transform = lambda value: math.log10(max(value, 10 ** lo))
    else:
        lo, hi = 0.0, max(flat, default=1.0) * 1.1
        if hi <= lo: hi = 1.0
        transform = lambda value: value
    ylo, yhi = (lo, hi) if log_scale else (lo, hi)
    map_y = lambda value: top + plot_h - (transform(value) - ylo) / (yhi - ylo) * plot_h
    map_x = lambda i: left + (plot_w * i / max(1, len(labels) - 1))
    pieces = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
        f'<text x="{width/2}" y="28" text-anchor="middle" font-family="sans-serif" font-size="17" font-weight="bold">{title}</text>',
        f'<text transform="translate(20 {top+plot_h/2}) rotate(-90)" text-anchor="middle" font-family="sans-serif" font-size="12">{y_label}</text>',
        f'<line x1="{left}" y1="{top+plot_h}" x2="{left+plot_w}" y2="{top+plot_h}" stroke="#333"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}" stroke="#333"/>',
    ]
    ticks = range(int(ylo), int(yhi) + 1) if log_scale else range(0, 6)
    for tick in ticks:
        value = 10 ** tick if log_scale else hi * tick / 5
        y = map_y(value)
        shown = f"10^{tick}" if log_scale else f"{value:.0f}"
        pieces.append(f'<line x1="{left-4}" y1="{y:.1f}" x2="{left+plot_w}" y2="{y:.1f}" stroke="#ddd"/>')
        pieces.append(f'<text x="{left-8}" y="{y+4:.1f}" text-anchor="end" font-family="sans-serif" font-size="10">{shown}</text>')
    for i, label in enumerate(labels):
        x = map_x(i)
        pieces.append(f'<text x="{x:.1f}" y="{top+plot_h+22}" text-anchor="middle" font-family="sans-serif" font-size="11">{label}</text>')
    for index, (name, values, color) in enumerate(series):
        coords = " ".join(f"{map_x(i):.1f},{map_y(value):.1f}" for i, value in enumerate(values))
        pieces.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for i, value in enumerate(values):
            pieces.append(f'<circle cx="{map_x(i):.1f}" cy="{map_y(value):.1f}" r="3.5" fill="{color}"/>')
        lx = left + 12 + (index % 3) * 210
        ly = height - 26
        pieces.append(f'<line x1="{lx}" y1="{ly-4}" x2="{lx+22}" y2="{ly-4}" stroke="{color}" stroke-width="3"/>')
        pieces.append(f'<text x="{lx+28}" y="{ly}" font-family="sans-serif" font-size="11">{name}</text>')
    pieces.append("</svg>")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(pieces))


def make_graphs(results_dir: str, geo_rows: list[dict], dim_rows: list[dict]) -> None:
    colors = {"R-Tree": "#0d6b62", "Secuencial": "#9c5410"}
    for operation, k, name, title in (
        ("radio", "", "radio", "Consultas de radio: bloques leídos por N"),
        ("knn", "20", "knn_k20", "k-NN (k=20): bloques leídos por N"),
    ):
        subset = [row for row in geo_rows if row["operation"] == operation and str(row["k"]) == k]
        labels = [str(n) for n in SIZES]
        series = []
        for method in ("R-Tree", "Secuencial"):
            series.append((method, [float(next(row["total_reads"] for row in subset
                                                if row["method"] == method and int(row["n"]) == n))
                                    for n in SIZES], colors[method]))
        svg_chart(os.path.join(results_dir, f"experimento5_{name}_io.svg"), title, labels,
                  series, "Lecturas promedio (escala log)", log_scale=True)
    labels = [str(d) for d in DIMENSIONS]
    series = []
    for method in ("R-Tree", "Secuencial"):
        series.append((method, [float(next(row["avg_ms"] for row in dim_rows
                                            if row["method"] == method and int(row["d"]) == d))
                                for d in DIMENSIONS], colors[method]))
    svg_chart(os.path.join(results_dir, "experimento5_dimensiones_ms.svg"),
              "k-NN (k=20): efecto de la dimensión", labels, series,
              "Tiempo promedio (ms)", log_scale=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=os.path.join(ROOT, "benchmarks", "results"))
    parser.add_argument("--queries", type=int, default=10)
    parser.add_argument("--dim-queries", type=int, default=5)
    parser.add_argument("--dim-n", type=int, default=10_000)
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    workspace = tempfile.mkdtemp(prefix="cs2042-exp5-")
    geographic_rows, dimensional_rows = [], []
    try:
        run_geographic(workspace, geographic_rows, args.queries)
        run_dimensions(workspace, dimensional_rows, args.dim_n, args.dim_queries)
        write_csv(os.path.join(args.out, "experimento5_espacial.csv"), geographic_rows)
        write_csv(os.path.join(args.out, "experimento5_dimensionalidad.csv"), dimensional_rows)
        make_graphs(args.out, geographic_rows, dimensional_rows)
    finally:
        import shutil

        shutil.rmtree(workspace, ignore_errors=True)
    print(f"Resultados guardados en {args.out}")


if __name__ == "__main__":
    main()
