"""rtree.py - R-Tree paginado en disco (seek/read/write + struct).

Diseño físico (páginas de 4096 B por defecto):
  Página 0      : metadata (magic, page_size, D, dtype, root, num_pages, height, count)
  Página >= 1   : nodo = cabecera (page_id, node_type, num_entries) + entradas de
                  longitud fija empaquetadas + espacio libre.
  Hoja     : <MBR, RID>        -> '<' + 2D*dtype + 'ii'   (RID = page_id, slot)
  Interno  : <MBR, child_pid>  -> '<' + 2D*dtype + 'i'
  MBR = (min_1..min_D, max_1..max_D); en 2D: (xmin, ymin, xmax, ymax) = (lon, lat, lon, lat).
"""
import heapq
import math
import os
import struct

HEADER = struct.Struct('<III')    # page_id, node_type (0 hoja / 1 interno), num_entries
META = struct.Struct('<4s6IQ')    # magic, page_size, D, dtype, root, num_pages, height, count
MAGIC = b'RTRE'
PAGE_SIZE = 4096
LEAF, INTERNAL = 0, 1


class DiskCounter:
    """Cuenta todos los bloques físicos transferidos, incluida metadata."""

    def __init__(self):
        self.disk_reads = 0
        self.disk_writes = 0

    def reset(self):
        self.disk_reads = 0
        self.disk_writes = 0

    def snapshot(self):
        return {'disk_reads': self.disk_reads, 'disk_writes': self.disk_writes}


# ---------- redondeo float32 (hacia afuera, para no perder falsos negativos) ----------
def _rt32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def _down32(x):
    y = _rt32(x)
    if y <= x:
        return y
    if y == 0.0:
        return -1.401298464324817e-45
    b = struct.unpack('<I', struct.pack('<f', y))[0]
    b = b - 1 if y > 0 else b + 1
    return struct.unpack('<f', struct.pack('<I', b))[0]


def _up32(x):
    return -_down32(-x)


# ---------- geometría ----------
def area(m, D):
    a = 1.0
    for i in range(D):
        a *= m[D + i] - m[i]
    return a


def union(a, b, D):
    return (tuple(min(a[i], b[i]) for i in range(D)) +
            tuple(max(a[D + i], b[D + i]) for i in range(D)))


def mbr_of(mbrs, D):
    it = iter(mbrs)
    r = next(it)
    for m in it:
        r = union(r, m, D)
    return r


def intersects(a, b, D):
    return all(a[i] <= b[D + i] and b[i] <= a[D + i] for i in range(D))


def mindist2(q, m, D):
    """MINDIST(Q, MBR) al cuadrado."""
    s = 0.0
    for i in range(D):
        if q[i] < m[i]:
            d = m[i] - q[i]
        elif q[i] > m[D + i]:
            d = q[i] - m[D + i]
        else:
            continue
        s += d * d
    return s


class RTree:
    def __init__(self, path, D=2, dtype='d', page_size=4096, counter=None):
        if page_size != PAGE_SIZE:
            raise ValueError(f'el R-Tree requiere páginas de {PAGE_SIZE} B')
        self.counter = counter or DiskCounter()
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        self.f = open(path, 'w+b' if new else 'r+b')
        if new:
            if dtype not in ('d', 'f'):
                raise ValueError("dtype debe ser 'd' (float64) o 'f' (float32)")
            self.page_size, self.D, self.dtype = page_size, D, dtype
            self.root, self.num_pages, self.height, self.count = 1, 2, 1, 0
            self._setup()
            self._write_node(1, LEAF, [])
            self._write_meta()
        else:
            self._read_meta()
            self._setup()

    # ---------- metadata ----------
    def _setup(self):
        c = self.dtype
        self.ifmt = struct.Struct(f'<{2 * self.D}{c}i')
        self.lfmt = struct.Struct(f'<{2 * self.D}{c}ii')
        self.max_internal = (self.page_size - HEADER.size) // self.ifmt.size
        self.max_leaf = (self.page_size - HEADER.size) // self.lfmt.size
        if min(self.max_internal, self.max_leaf) < 3:
            raise ValueError(f'D={self.D} con dtype={c} no cabe (M<3) en {self.page_size} B')

    def _write_meta(self):
        buf = bytearray(self.page_size)
        META.pack_into(buf, 0, MAGIC, self.page_size, self.D, 0 if self.dtype == 'd' else 1,
                       self.root, self.num_pages, self.height, self.count)
        self.f.seek(0)
        self.f.write(buf)
        self.counter.disk_writes += 1

    def _read_meta(self):
        self.f.seek(0)
        buf = self.f.read(PAGE_SIZE)
        self.counter.disk_reads += 1
        if len(buf) != PAGE_SIZE:
            raise ValueError('página de metadata truncada')
        magic, ps, D, dt, root, npg, h, cnt = META.unpack_from(buf)
        if magic != MAGIC:
            raise ValueError('archivo no es un R-Tree')
        if ps != PAGE_SIZE:
            raise ValueError(f'el R-Tree guardado usa páginas de {ps} B, se requieren {PAGE_SIZE} B')
        self.page_size, self.D, self.dtype = ps, D, 'd' if dt == 0 else 'f'
        self.root, self.num_pages, self.height, self.count = root, npg, h, cnt

    def info(self):
        return {'D': self.D, 'dtype': self.dtype, 'page_size': self.page_size,
                'M_internal': self.max_internal, 'M_leaf': self.max_leaf,
                'height': self.height, 'num_pages': self.num_pages, 'count': self.count}

    def close(self):
        self._write_meta()
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---------- E/S de páginas ----------
    def _round(self, mbr):
        if self.dtype == 'd':
            return mbr
        D = self.D
        return tuple(_down32(x) for x in mbr[:D]) + tuple(_up32(x) for x in mbr[D:])

    def _alloc(self):
        pid = self.num_pages
        self.num_pages += 1
        return pid

    def _read_node(self, pid):
        self.f.seek(pid * self.page_size)
        buf = self.f.read(self.page_size)
        self.counter.disk_reads += 1
        if len(buf) != self.page_size:
            raise ValueError(f'página {pid} truncada')
        _, ntype, n = HEADER.unpack_from(buf, 0)
        fmt = self.lfmt if ntype == LEAF else self.ifmt
        off, entries, k = HEADER.size, [], 2 * self.D
        for _ in range(n):
            v = fmt.unpack_from(buf, off)
            off += fmt.size
            entries.append((v[:k], tuple(v[k:]) if ntype == LEAF else v[k]))
        return ntype, entries

    def _write_node(self, pid, ntype, entries):
        buf = bytearray(self.page_size)
        HEADER.pack_into(buf, 0, pid, ntype, len(entries))
        fmt = self.lfmt if ntype == LEAF else self.ifmt
        off = HEADER.size
        for mbr, ptr in entries:
            mbr = self._round(mbr)
            if ntype == LEAF:
                fmt.pack_into(buf, off, *mbr, ptr[0], ptr[1])
            else:
                fmt.pack_into(buf, off, *mbr, ptr)
            off += fmt.size
        self.f.seek(pid * self.page_size)
        self.f.write(buf)
        self.counter.disk_writes += 1

    # ---------- inserción ----------
    def insert_point(self, coords, rid):
        c = tuple(float(x) for x in coords)
        self.insert(c + c, rid)

    def insert(self, mbr, rid):
        entry = (tuple(float(x) for x in mbr), (int(rid[0]), int(rid[1])))
        new_mbr, split = self._insert(self.root, entry)
        if split:   # la raíz se dividió: nueva raíz
            new_root = self._alloc()
            self._write_node(new_root, INTERNAL, [(new_mbr, self.root), split])
            self.root = new_root
            self.height += 1
        self.count += 1
        self._write_meta()

    def _insert(self, pid, entry):
        ntype, entries = self._read_node(pid)
        if ntype == LEAF:
            entries.append(entry)
            cap = self.max_leaf
        else:
            i = self._choose(entries, entry[0])
            child = entries[i][1]
            new_mbr, split = self._insert(child, entry)
            entries[i] = (new_mbr, child)
            if split:
                entries.append(split)
            cap = self.max_internal
        if len(entries) > cap:
            g1, g2 = self._split(entries, cap)
            sib = self._alloc()
            self._write_node(pid, ntype, g1)
            self._write_node(sib, ntype, g2)
            return mbr_of([e[0] for e in g1], self.D), (mbr_of([e[0] for e in g2], self.D), sib)
        self._write_node(pid, ntype, entries)
        return mbr_of([e[0] for e in entries], self.D), None

    def _choose(self, entries, mbr):
        """Rama con menor ampliación de área (desempate: menor área)."""
        D, best, bi = self.D, None, 0
        for i, (m, _) in enumerate(entries):
            a = area(m, D)
            key = (area(union(m, mbr, D), D) - a, a)
            if best is None or key < best:
                best, bi = key, i
        return bi

    def _split(self, entries, cap):
        """Quadratic Split (Guttman)."""
        D, n = self.D, len(entries)
        mn = max(1, int(0.4 * cap))
        worst, s1, s2 = -math.inf, 0, 1
        for i in range(n):
            for j in range(i + 1, n):
                a, b = entries[i][0], entries[j][0]
                d = area(union(a, b, D), D) - area(a, D) - area(b, D)
                if d > worst:
                    worst, s1, s2 = d, i, j
        g1, g2 = [entries[s1]], [entries[s2]]
        m1, m2 = entries[s1][0], entries[s2][0]
        rest = [e for k, e in enumerate(entries) if k not in (s1, s2)]
        while rest:
            if len(g1) + len(rest) == mn:
                g1 += rest
                break
            if len(g2) + len(rest) == mn:
                g2 += rest
                break
            best, bi = -1.0, 0
            for k, e in enumerate(rest):
                d1 = area(union(m1, e[0], D), D) - area(m1, D)
                d2 = area(union(m2, e[0], D), D) - area(m2, D)
                if abs(d1 - d2) > best:
                    best, bi = abs(d1 - d2), k
            e = rest.pop(bi)
            u1, u2 = union(m1, e[0], D), union(m2, e[0], D)
            d1, d2 = area(u1, D) - area(m1, D), area(u2, D) - area(m2, D)
            if (d1, area(m1, D), len(g1)) <= (d2, area(m2, D), len(g2)):
                g1.append(e)
                m1 = u1
            else:
                g2.append(e)
                m2 = u2
        return g1, g2

    # ---------- búsquedas ----------
    def search_window(self, w):
        """Rango por ventana W = (min_1..min_D, max_1..max_D). Poda MBRi ∩ W = ∅."""
        out, stack = [], [self.root]
        while stack:
            ntype, entries = self._read_node(stack.pop())
            for m, p in entries:
                if intersects(m, w, self.D):
                    if ntype == LEAF:
                        out.append((p, m))
                    else:
                        stack.append(p)
        return out

    def search_radius(self, q, r):
        """Puntos a distancia <= r de Q. Poda ramas con MINDIST(Q, MBR) > r."""
        r2, out, stack = r * r, [], [self.root]
        while stack:
            ntype, entries = self._read_node(stack.pop())
            for m, p in entries:
                d2 = mindist2(q, m, self.D)
                if d2 <= r2:
                    if ntype == LEAF:
                        out.append((math.sqrt(d2), p, m))
                    else:
                        stack.append(p)
        out.sort(key=lambda t: t[0])
        return out

    def knn(self, q, k):
        """k-NN best-first con cola de prioridad por MINDIST. Devuelve (dist, rid, mbr).
        Los nodos con MINDIST mayor que el k-ésimo vecino nunca salen de la cola
        (se detiene al obtener k), por lo que sus subárboles no se leen."""
        heap, cnt, res = [(0.0, 0, False, self.root)], 1, []
        while heap and len(res) < k:
            d2, _, is_obj, item = heapq.heappop(heap)
            if is_obj:
                res.append((math.sqrt(d2), item[1], item[0]))
                continue
            ntype, entries = self._read_node(item)
            for m, p in entries:
                d = mindist2(q, m, self.D)
                if ntype == LEAF:
                    heapq.heappush(heap, (d, cnt, True, (m, p)))
                else:
                    heapq.heappush(heap, (d, cnt, False, p))
                cnt += 1
        return res
