"""Wiring: build a QueryEngine from environment configuration.

The storage backend is chosen here and nowhere else, which is what keeps the
engine independent of whichever implementation the storage and index modules
end up shipping.

    QE_STORAGE_BACKEND=memory   in-process stand-in (default, development)
    QE_STORAGE_BACKEND=disk     real 4 KB blocks through the storage module
    QE_CATALOG_PATH=./data/catalog.json
    QE_DATA_DIR=./data          confines the paths COPY may read
    QE_TABLE_DIR=./data/tables  where the disk backend keeps one .bin per table
    QE_BLK01_PATH=../../storage the storage module to bind against

The disk backend pairs real on-disk tables with in-memory indexes: the B+ tree
and the dynamic hash are a separate deliverable, so until they land the planner
still gets working index access paths without anyone faking a file format.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .catalog import Catalog
from .engine import QueryEngine
from .storage.memory import MemoryIndexStore, MemoryTableStore
from .storage.port import IOCounter

DEFAULT_CATALOG_PATH = "data/catalog.json"
DEFAULT_TABLE_DIR = "data/tables"


@dataclass(frozen=True)
class Settings:
    backend: str = "memory"
    catalog_path: str | None = DEFAULT_CATALOG_PATH
    data_dir: str | None = None
    table_dir: str = DEFAULT_TABLE_DIR
    physical_path: str | None = None

    @staticmethod
    def from_env() -> Settings:
        path = os.getenv("QE_CATALOG_PATH", DEFAULT_CATALOG_PATH)
        data_dir = os.getenv("QE_DATA_DIR", "").strip()
        return Settings(
            backend=os.getenv("QE_STORAGE_BACKEND", "memory").strip().lower(),
            catalog_path=None if path.lower() in ("", "none", ":memory:") else path,
            data_dir=data_dir or None,
            table_dir=os.getenv("QE_TABLE_DIR", DEFAULT_TABLE_DIR),
            physical_path=os.getenv("QE_BLK01_PATH") or None,
        )


def build_engine(settings: Settings | None = None) -> QueryEngine:
    settings = settings or Settings.from_env()
    catalog = Catalog(settings.catalog_path)
    io = IOCounter()

    if settings.backend == "memory":
        storage = MemoryTableStore(io)
        indexes = MemoryIndexStore(io)
    elif settings.backend == "disk":
        from .storage.blk01 import load
        from .storage.diskstore import DiskTableStore

        storage = DiskTableStore(io, settings.table_dir, load(settings.physical_path))
        indexes = MemoryIndexStore(io)
    else:
        raise ValueError(
            f"backend de almacenamiento desconocido: '{settings.backend}'. "
            "Registra el adaptador en bootstrap.build_engine antes de usarlo."
        )

    engine = QueryEngine(catalog, storage, indexes, io, settings.data_dir)
    _reopen_existing_tables(engine, storage)
    return engine


def _reopen_existing_tables(engine: QueryEngine, storage) -> None:
    """Let the storage backend re-attach to tables the catalog already knows."""
    for schema in engine.catalog.tables():
        storage.create_table(schema)
