# Acceso a disco por bloques fijos: seek(page_id * page_size) + struct, sin pickle ni .read() global.
# Cada bloque leído/escrito suma en disk_reads / disk_writes.
# StreamWriter/StreamReader reparten un flujo de bytes variable sobre bloques consecutivos.

from __future__ import annotations

import os

DEFAULT_PAGE_SIZE = 4096


class DiskCounter:
    # Contador mínimo, compatible con el ``IOCounter`` del motor.

    def __init__(self) -> None:
        self.disk_reads = 0
        self.disk_writes = 0

    def reset(self) -> None:
        self.disk_reads = 0
        self.disk_writes = 0

    def snapshot(self) -> tuple[int, int]:
        return self.disk_reads, self.disk_writes


class PagedFile:
    # Archivo binario visto como un arreglo de bloques de ``page_size`` bytes.

    def __init__(self, path: str, counter, page_size: int = DEFAULT_PAGE_SIZE,
                 create: bool = False):
        self.path = path
        self.page_size = page_size
        self.counter = counter
        if create or not os.path.exists(path):
            self._handle = open(path, "w+b")  # noqa: SIM115 -- se cierra en close()
        else:
            self._handle = open(path, "r+b")  # noqa: SIM115

    @property
    def page_count(self) -> int:
        return os.fstat(self._handle.fileno()).st_size // self.page_size

    def read_page(self, page_id: int) -> bytes:
        self._handle.seek(page_id * self.page_size)
        data = self._handle.read(self.page_size)
        self.counter.disk_reads += 1
        if len(data) < self.page_size:
            data = data.ljust(self.page_size, b"\x00")
        return data

    def write_page(self, page_id: int, data: bytes | bytearray) -> None:
        if len(data) != self.page_size:
            raise ValueError(
                f"un bloque debe medir {self.page_size} B y se recibieron {len(data)}"
            )
        self._handle.seek(page_id * self.page_size)
        self._handle.write(data)
        self.counter.disk_writes += 1

    def flush(self) -> None:
        self._handle.flush()

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.flush()
            self._handle.close()

    def __enter__(self) -> PagedFile:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


class StreamWriter:
    # Escribe un flujo de bytes en bloques consecutivos desde ``first_page``.

    def __init__(self, paged: PagedFile, first_page: int = 0):
        self._paged = paged
        self._first_page = first_page
        self._page = first_page
        self._buffer = bytearray()
        self._offset = 0

    def tell(self) -> int:
        return self._offset

    def write(self, data: bytes) -> None:
        self._buffer += data
        self._offset += len(data)
        size = self._paged.page_size
        while len(self._buffer) >= size:
            self._paged.write_page(self._page, bytes(self._buffer[:size]))
            del self._buffer[:size]
            self._page += 1

    def close(self) -> int:
        # Escribe el último bloque (rellenado con ceros); devuelve páginas usadas.
        if self._buffer:
            self._paged.write_page(self._page, bytes(self._buffer).ljust(self._paged.page_size,
                                                                         b"\x00"))
            self._buffer = bytearray()
            self._page += 1
        return self._page - self._first_page


class StreamReader:
    # Lee rangos de un flujo paginado contando cada bloque que toca.

    def __init__(self, paged: PagedFile, first_page: int = 0):
        self._paged = paged
        self._first_page = first_page
        self._cached_id = -1
        self._cached = b""
        self._cursor = 0

    def _page(self, relative: int) -> bytes:
        if relative != self._cached_id:
            self._cached = self._paged.read_page(self._first_page + relative)
            self._cached_id = relative
        return self._cached

    def read_at(self, offset: int, length: int) -> bytes:
        size = self._paged.page_size
        out = bytearray()
        while length > 0:
            relative, inside = divmod(offset, size)
            page = self._page(relative)
            take = min(length, size - inside)
            out += page[inside:inside + take]
            offset += take
            length -= take
        return bytes(out)

    # -- lectura secuencial (bloques SPIMI) -------------------------------

    def seek(self, offset: int) -> None:
        self._cursor = offset

    def read(self, length: int) -> bytes:
        data = self.read_at(self._cursor, length)
        self._cursor += length
        return data
