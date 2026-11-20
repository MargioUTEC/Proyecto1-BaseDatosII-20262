# Adaptador del índice invertido (Bloque B) al puerto de índices del motor.
# Un directorio <tabla_dir>/<nombre>.inv por índice; las claves son los RID de la tabla.
# INSERT posteriores: se fusionan como corrida SPIMI antes de la siguiente búsqueda.
# DELETE: la entrada queda y se descarta al leer la fila (fetch devuelve None).

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Iterable

from ..catalog import IndexKind, IndexMeta, TableSchema
from ..errors import CatalogError, StorageUnavailableError
from ..textsearch import DEFAULT_LANGUAGE, SCORINGS, analyzer, textual
from ..types import TypeKind
from .port import RID, IOCounter

MERGE_THRESHOLD = 5_000


class DiskInvertedIndex:
    def __init__(self, io: IOCounter, data_dir: str, page_size: int = 4096):
        self.io = io
        self.data_dir = os.path.abspath(data_dir)
        self.page_size = page_size
        os.makedirs(self.data_dir, exist_ok=True)
        self._open: dict[str, object] = {}
        self._meta: dict[str, IndexMeta] = {}
        self._pending: dict[str, list[tuple[RID, str]]] = {}

    # -- enrutamiento -----------------------------------------------------

    def why_not(self, meta: IndexMeta, schema: TableSchema) -> str | None:
        if meta.kind is not IndexKind.INVERTED:
            return "no es INVERTED"
        column = schema.column(meta.column)
        if column.type.kind not in (TypeKind.CHAR, TypeKind.VARCHAR):
            return f"{schema.name}.{meta.column} debe ser CHAR o VARCHAR para indexar texto"
        return None

    def accepts(self, meta: IndexMeta, schema: TableSchema) -> bool:
        return self.why_not(meta, schema) is None

    @staticmethod
    def validate_options(meta: IndexMeta) -> None:
        scoring = str(meta.option("scoring", "TFIDF_COSINE")).upper()
        if scoring not in SCORINGS:
            raise CatalogError(f"scoring '{scoring}' no soportado (opciones: {', '.join(SCORINGS)})")
        language = str(meta.option("language", DEFAULT_LANGUAGE)).lower()
        if language not in ("spanish", "english"):
            raise CatalogError(f"language '{language}' no soportado (spanish | english)")
        unknown = {key for key, _ in meta.options} - {"scoring", "language"}
        if unknown:
            raise CatalogError(f"opciones desconocidas para INVERTED: {', '.join(sorted(unknown))}")

    # -- ciclo de vida ----------------------------------------------------

    def create_index(self, meta: IndexMeta, schema: TableSchema) -> None:
        refusal = self.why_not(meta, schema)
        if refusal:
            raise StorageUnavailableError(refusal)
        self.validate_options(meta)
        key = meta.name.lower()
        self._close(key)
        shutil.rmtree(self._path(meta.name), ignore_errors=True)
        self._meta[key] = meta
        self._pending[key] = []
        self._build(key, [])

    def reopen_index(self, meta: IndexMeta, schema: TableSchema) -> None:
        refusal = self.why_not(meta, schema)
        if refusal:
            raise StorageUnavailableError(refusal)
        key = meta.name.lower()
        self._meta[key] = meta
        self._pending[key] = []
        path = self._path(meta.name)
        if os.path.exists(os.path.join(path, "vocab.dic")):
            self._open[key] = textual().InvertedIndex(path, self.io, self.page_size)
        else:
            self._build(key, [])

    def drop_index(self, name: str) -> None:
        key = name.lower()
        self._close(key)
        self._meta.pop(key, None)
        self._pending.pop(key, None)
        shutil.rmtree(self._path(name), ignore_errors=True)

    def bulk_load(self, name: str, entries: Iterable[tuple[object, RID]]) -> None:
        # Construye el indice completo con SPIMI a partir de (texto, rid).
        key = name.lower()
        self._pending[key] = []
        self._build(key, ((rid, text) for text, rid in entries if text is not None))

    def _build(self, key: str, documents) -> None:
        meta = self._meta[key]
        self._close(key)
        language = str(meta.option("language", DEFAULT_LANGUAGE)).lower()
        self._open[key] = textual().InvertedIndex.build(
            self._path(meta.name), documents, analyzer(language), self.io, self.page_size
        )

    def _close(self, key: str) -> None:
        index = self._open.pop(key, None)
        if index is not None:
            index.close()

    # -- mantenimiento ----------------------------------------------------

    def insert(self, name: str, key, rid: RID) -> None:
        if key is None:
            return
        name_key = name.lower()
        self._pending.setdefault(name_key, []).append((rid, key))
        if len(self._pending[name_key]) >= MERGE_THRESHOLD:
            self._merge_pending(name_key)

    def delete(self, name: str, key, rid: RID) -> None:
        # La entrada vieja se filtra al leer: la fila borrada devuelve None.
        return None

    def _merge_pending(self, key: str) -> None:
        pending = self._pending.get(key)
        if pending:
            self._pending[key] = []
            self._entry(key).add(pending)

    # -- consultas --------------------------------------------------------

    def text_search(self, name: str, query: str, top_k: int | None):
        key = name.lower()
        self._merge_pending(key)
        return self._entry(key).search(query, top_k)

    def search(self, name: str, key) -> list[RID]:
        raise StorageUnavailableError("un indice INVERTED se consulta con MATCH")

    def range_search(self, name: str, lower, upper) -> list[RID]:
        raise StorageUnavailableError("un indice INVERTED no admite rangos")

    def height(self, name: str) -> int:
        # directorio en RAM + 1 pagina de vocab.dic por termino
        return 1

    def entry_count(self, name: str) -> int:
        key = name.lower()
        return self._entry(key).n_docs + len(self._pending.get(key, []))

    def describe_index(self, name: str) -> dict:
        key = name.lower()
        self._merge_pending(key)
        return self._entry(key).describe()

    def _entry(self, key: str):
        try:
            return self._open[key.lower()]
        except KeyError:
            raise StorageUnavailableError(f"indice invertido '{key}' no esta abierto") from None

    def _path(self, name: str) -> str:
        return os.path.join(self.data_dir, f"{name.lower()}.inv")

    def close(self) -> None:
        for key in list(self._open):
            with contextlib.suppress(Exception):
                self._merge_pending(key)
            self._close(key)
