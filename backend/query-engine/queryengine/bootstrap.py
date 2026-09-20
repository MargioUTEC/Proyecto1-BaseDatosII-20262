"""Wiring: build a QueryEngine from environment configuration.

The storage backend is chosen here and nowhere else, which is what keeps the
engine independent of whichever implementation the storage and index modules
end up shipping.

    QE_STORAGE_BACKEND=memory   in-process stand-in (default, development)
    QE_CATALOG_PATH=./data/catalog.json
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .catalog import Catalog
from .engine import QueryEngine
from .storage.memory import MemoryIndexStore, MemoryTableStore
from .storage.port import IOCounter

DEFAULT_CATALOG_PATH = "data/catalog.json"


@dataclass(frozen=True)
class Settings:
    backend: str = "memory"
    catalog_path: str | None = DEFAULT_CATALOG_PATH

    @staticmethod
    def from_env() -> Settings:
        path = os.getenv("QE_CATALOG_PATH", DEFAULT_CATALOG_PATH)
        return Settings(
            backend=os.getenv("QE_STORAGE_BACKEND", "memory").strip().lower(),
            catalog_path=None if path.lower() in ("", "none", ":memory:") else path,
        )


def build_engine(settings: Settings | None = None) -> QueryEngine:
    settings = settings or Settings.from_env()
    catalog = Catalog(settings.catalog_path)
    io = IOCounter()

    if settings.backend == "memory":
        storage = MemoryTableStore(io)
        indexes = MemoryIndexStore(io)
    else:
        raise ValueError(
            f"backend de almacenamiento desconocido: '{settings.backend}'. "
            "Registra el adaptador en bootstrap.build_engine antes de usarlo."
        )

    engine = QueryEngine(catalog, storage, indexes, io)
    _reopen_existing_tables(engine, storage)
    return engine


def _reopen_existing_tables(engine: QueryEngine, storage) -> None:
    """Let the storage backend re-attach to tables the catalog already knows."""
    for schema in engine.catalog.tables():
        storage.create_table(schema)
