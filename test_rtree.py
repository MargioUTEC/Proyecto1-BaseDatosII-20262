"""Pruebas: R-Tree vs fuerza bruta (rango, radio, k-NN) + persistencia."""
import math
import os
import random
import tempfile

from rtree import RTree


def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def run(D, dtype, N, tol):
    random.seed(1)
    pts = [tuple(random.uniform(-1, 1) for _ in range(D)) for _ in range(N)]
    path = os.path.join(tempfile.mkdtemp(), f'rt_{D}_{dtype}.idx')
    with RTree(path, D=D, dtype=dtype) as t:
        for i, p in enumerate(pts):
            t.insert_point(p, (i // 50, i % 50))
        info = t.info()
    t = RTree(path)  # reabrir desde disco
    assert t.count == N and t.D == D and t.dtype == dtype

    for _ in range(20):
        q = tuple(random.uniform(-1, 1) for _ in range(D))
        # k-NN
        k = 10
        t.counter.reset()
        got = t.knn(q, k)
        reads = t.counter.disk_reads
        exp = sorted(dist(q, p) for p in pts)[:k]
        assert all(abs(g[0] - e) <= tol for g, e in zip(got, exp)), (D, dtype, 'knn')
        # radio
        r = sorted(dist(q, p) for p in pts)[min(30, N - 1)]
        got_r = {g[1] for g in t.search_radius(q, r)}
        must = {(i // 50, i % 50) for i, p in enumerate(pts) if dist(q, p) <= r - tol}
        may = {(i // 50, i % 50) for i, p in enumerate(pts) if dist(q, p) <= r + tol}
        assert must <= got_r <= may, (D, dtype, 'radio')
    print(f'OK D={D:>3} {dtype} N={N:>6}  M_int={info["M_internal"]:>3} '
          f'M_hoja={info["M_leaf"]:>3} h={info["height"]} paginas={info["num_pages"]} '
          f'(knn leyo {reads} pags)')
    t.close()


if __name__ == '__main__':
    run(2, 'd', 20000, 1e-12)
    run(4, 'f', 3000, 1e-4)
    run(8, 'f', 2000, 1e-4)
    run(32, 'f', 500, 1e-4)
    run(128, 'f', 200, 1e-4)
    # ventana 2D
    random.seed(2)
    pts = [(random.uniform(-80, -70), random.uniform(-15, -10)) for _ in range(5000)]
    p = os.path.join(tempfile.mkdtemp(), 'w.idx')
    with RTree(p, D=2, dtype='d') as t:
        for i, c in enumerate(pts):
            t.insert_point(c, (i, 0))
        w = (-76.0, -13.0, -74.0, -12.0)
        got = {r[0][0] for r in t.search_window(w)}
        exp = {i for i, c in enumerate(pts) if w[0] <= c[0] <= w[2] and w[1] <= c[1] <= w[3]}
        assert got == exp
    print('OK ventana 2D')