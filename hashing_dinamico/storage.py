from __future__ import annotations

import base64
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

try:
    import requests
except ImportError:
    requests = None

#tamaño de bloque que usamos si no nos dicen otro
DEFAULT_PAGE_SIZE = 4096


@dataclass
class DiskCounter:
    disk_reads: int = 0
    disk_writes: int = 0

    def reset(self) -> None:
        self.disk_reads = 0
        self.disk_writes = 0

    def get_metrics(self) -> dict:
        return {"disk_reads": self.disk_reads, "disk_writes": self.disk_writes}


#esta es la receta que debe cumplir cualquier forma de guardar paginas
class StorageBackend(ABC):
    page_size: int
    counter: DiskCounter

    @abstractmethod
    def allocate_page(self) -> int:
        pass

    @abstractmethod
    def read_page(self, page_id: int) -> bytes:
        pass

    @abstractmethod
    def write_page(self, page_id: int, data: bytes) -> None:
        pass

    def get_total_pages(self) -> int:
        raise NotImplementedError


#esta guarda todo en un archivo local, para probar sin depender de nadie mas
class EmbeddedDiskManager(StorageBackend):
    def __init__(
        self,
        db_path: str,
        page_size: int = DEFAULT_PAGE_SIZE,
        counter: Optional[DiskCounter] = None,
    ):
        self.db_path = db_path
        self.page_size = page_size
        self.counter = counter if counter is not None else DiskCounter()
        if not os.path.exists(self.db_path):
            with open(self.db_path, "wb"):
                pass

    def read_page(self, page_id: int) -> bytes:
        offset = page_id * self.page_size
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Archivo no encontrado: {self.db_path}")
        file_size = os.path.getsize(self.db_path)
        if offset >= file_size:
            raise IndexError(
                f"Page ID {page_id} fuera de rango. Tamaño actual: {file_size} bytes"
            )
        with open(self.db_path, "rb") as f:
            f.seek(offset)
            raw_bytes = f.read(self.page_size)
        self.counter.disk_reads += 1
        if len(raw_bytes) < self.page_size:
            raw_bytes = raw_bytes.ljust(self.page_size, b"\x00")
        return raw_bytes

    def write_page(self, page_id: int, data: bytes) -> None:
        if len(data) != self.page_size:
            raise ValueError(
                f"El bloque debe medir {self.page_size} bytes (recibido: {len(data)})"
            )
        offset = page_id * self.page_size
        with open(self.db_path, "r+b") as f:
            f.seek(offset)
            f.write(data)
            f.flush()
        self.counter.disk_writes += 1

    def allocate_page(self) -> int:
        file_size = os.path.getsize(self.db_path)
        new_page_id = file_size // self.page_size
        empty_block = b"\x00" * self.page_size
        with open(self.db_path, "a+b") as f:
            f.seek(new_page_id * self.page_size)
            f.write(empty_block)
            f.flush()
        self.counter.disk_writes += 1
        return new_page_id

    def get_total_pages(self) -> int:
        return os.path.getsize(self.db_path) // self.page_size


#esta habla por internet con el servicio de storage del compañero, cuando ya esten los endpoints nuevos
class RemoteStorageClient(StorageBackend):
    def __init__(
        self,
        base_url: str,
        page_size: int = DEFAULT_PAGE_SIZE,
        counter: Optional[DiskCounter] = None,
        timeout: float = 5.0,
    ):
        if requests is None:
            raise RuntimeError(
                "Falta el paquete 'requests'. Agrégalo a requirements.txt "
                "para poder usar RemoteStorageClient."
            )
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.counter = counter if counter is not None else DiskCounter()
        self.timeout = timeout

    def allocate_page(self) -> int:
        resp = requests.post(f"{self.base_url}/pages/allocate", timeout=self.timeout)
        resp.raise_for_status()
        self.counter.disk_writes += 1
        return resp.json()["page_id"]

    def read_page(self, page_id: int) -> bytes:
        resp = requests.get(f"{self.base_url}/pages/{page_id}/raw", timeout=self.timeout)
        resp.raise_for_status()
        self.counter.disk_reads += 1
        return base64.b64decode(resp.json()["data_base64"])

    def write_page(self, page_id: int, data: bytes) -> None:
        if len(data) != self.page_size:
            raise ValueError(
                f"El bloque debe medir {self.page_size} bytes (recibido: {len(data)})"
            )
        payload = {"data_base64": base64.b64encode(data).decode("ascii")}
        resp = requests.put(
            f"{self.base_url}/pages/{page_id}/raw", json=payload, timeout=self.timeout
        )
        resp.raise_for_status()
        self.counter.disk_writes += 1

    def get_total_pages(self) -> int:
        raise NotImplementedError(
            "El microservicio de Storage no expone el total de paginas todavia"
        )


#esta funcion decide cual de las dos formas de guardar usar
def build_storage_backend(
    mode: str,
    db_path: str = "hash_index.bin",
    base_url: str = "http://storage-service:8000",
    page_size: int = DEFAULT_PAGE_SIZE,
    counter: Optional[DiskCounter] = None,
) -> StorageBackend:
    if mode == "remote":
        return RemoteStorageClient(base_url=base_url, page_size=page_size, counter=counter)
    if mode == "embedded":
        return EmbeddedDiskManager(db_path=db_path, page_size=page_size, counter=counter)
    raise ValueError(f"Modo de storage desconocido: {mode!r} (usa 'embedded' o 'remote')")
