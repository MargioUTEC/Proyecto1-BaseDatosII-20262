"""
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
LEAF, INTERNAL = 0, 1


class DiskCounter:
    """Cuenta bloques físicos leídos/escritos (la página 0 de metadata no se cuenta)."""

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

    def _read_meta(self):
        self.f.seek(0)
        magic, ps, D, dt, root, npg, h, cnt = META.unpack(self.f.read(META.size))
        if magic != MAGIC:
            raise ValueError('archivo no es un R-Tree')
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

# AQUI ME QUEDE ME FALTA LO DE E/S PARA LAS PAGINAS PERO TENGO SUEÑO XD