"""Adaptador del indice IVF, que vive en el bloque C y responde por HTTP.

La columna indexada guarda la ruta relativa de la imagen, no el vector. Es la
misma idea con la que el R-Tree indexa una columna de texto con POINT(lon lat):
la tabla guarda la referencia y la estructura guarda la geometria. Aqui la
estructura guarda los 128 flotantes del rostro.

Con esa ruta el adaptador arma el mapa ruta -> RID al crear el indice, que es lo
unico que necesita para traducir en las dos direcciones: el bloque C contesta
con sus docid y el motor trabaja con sus RID.
"""

from __future__ import annotations

from collections.abc import Iterable

from .. import vectorsearch
from ..catalog import IndexKind, IndexMeta, TableSchema
from ..errors import StorageUnavailableError
from ..types import TypeKind
from .port import RID, IOCounter

MODOS = ("face", "bovw")


class VectorIndexAdapter:
    def __init__(self, io: IOCounter):
        self.io = io
        self._indices: dict[str, dict] = {}

    # -- contrato comun de los indices ----------------------------------

    def why_not(self, meta: IndexMeta, schema: TableSchema) -> str | None:
        if meta.kind is not IndexKind.IVF:
            return "no es IVF"
        if schema.column(meta.column).type.kind not in (TypeKind.CHAR, TypeKind.VARCHAR):
            return (f"{schema.name}.{meta.column} debe guardar la ruta de la imagen "
                    "en CHAR/VARCHAR")
        modo = str(dict(meta.options).get("mode", "face")).lower()
        if modo not in MODOS:
            return f"mode debe ser {' o '.join(MODOS)}, y se dio '{modo}'"
        return None

    def accepts(self, meta: IndexMeta, schema: TableSchema) -> bool:
        return self.why_not(meta, schema) is None

    def create_index(self, meta: IndexMeta, schema: TableSchema) -> None:
        refusal = self.why_not(meta, schema)
        if refusal:
            raise StorageUnavailableError(refusal)
        opciones = dict(meta.options)
        self._indices[meta.name.lower()] = {
            "meta": meta,
            "modo": str(opciones.get("mode", "face")).lower(),
            "nprobe": int(opciones.get("nprobe", 4)),
            "max_candidatos": int(opciones.get("max_candidates", 500)),
            "por_ruta": {},
            "por_docid": {},
        }

    def reopen_index(self, meta: IndexMeta, schema: TableSchema) -> None:
        self.create_index(meta, schema)

    def drop_index(self, name: str) -> None:
        self._indices.pop(name.lower(), None)

    def insert(self, name: str, key, rid: RID) -> None:
        indice = self._entrada(name)
        ruta = _normalizar(key)
        if ruta:
            indice["por_ruta"][ruta] = rid

    def delete(self, name: str, key, rid: RID) -> None:
        indice = self._entrada(name)
        ruta = _normalizar(key)
        if indice["por_ruta"].get(ruta) == rid:
            del indice["por_ruta"][ruta]
            indice["por_docid"] = {}

    def bulk_load(self, name: str, entries: Iterable[tuple[object, RID]]) -> None:
        indice = self._entrada(name)
        for clave, rid in entries:
            ruta = _normalizar(clave)
            if ruta:
                indice["por_ruta"][ruta] = rid
        indice["por_docid"] = {}

    def search(self, name: str, key) -> list[RID]:
        indice = self._entrada(name)
        rid = indice["por_ruta"].get(_normalizar(key))
        return [rid] if rid is not None else []

    def range_search(self, name: str, lower, upper) -> list[RID]:
        raise StorageUnavailableError("un indice IVF no responde busquedas por rango")

    def height(self, name: str) -> int:
        return 1

    def entry_count(self, name: str) -> int:
        return len(self._entrada(name)["por_ruta"])

    def describe_index(self, name: str) -> dict:
        indice = self._entrada(name)
        return {"kind": IndexKind.IVF.value, "mode": indice["modo"],
                "nprobe": indice["nprobe"], "entries": len(indice["por_ruta"]),
                "servicio": vectorsearch.base_url()}

    # -- lo que usa el planificador vectorial ---------------------------

    def vector_search(self, name: str, vector, k: int, nprobe: int | None = None
                      ) -> tuple[list[tuple[RID, float]], dict]:
        """Post-filtering: el IVF sondea sus listas y devuelve los mejores."""
        indice = self._entrada(name)
        respuesta = vectorsearch.buscar(
            vector, indice["modo"], k,
            indice["nprobe"] if nprobe is None else nprobe,
            indice["max_candidatos"],
        )
        filas = self._traducir(indice, respuesta.get("results", []))
        return filas, _telemetria(respuesta)

    def vector_score(self, name: str, vector, rids: list[RID]
                     ) -> tuple[list[tuple[RID, float]], dict]:
        """Pre-filtering: el motor ya acoto las filas y solo pide su similitud."""
        indice = self._entrada(name)
        docids = self._docids(indice, rids)
        if not docids:
            return [], {"candidates": 0, "search_ms": 0.0}
        respuesta = vectorsearch.puntuar(vector, indice["modo"], list(docids))
        por_docid = {docid: rid for rid, docid in zip(rids, docids, strict=False)}
        filas = [(por_docid[fila["id"]], float(fila["similarity"]))
                 for fila in respuesta.get("scores", []) if fila["id"] in por_docid]
        filas.sort(key=lambda par: -par[1])
        return filas, _telemetria(respuesta)

    def _traducir(self, indice: dict, filas: list[dict]) -> list[tuple[RID, float]]:
        traducidas = []
        for fila in filas:
            rid = indice["por_ruta"].get(_normalizar(fila.get("path")))
            if rid is not None:
                traducidas.append((rid, float(fila["similarity"])))
        return traducidas

    def _docids(self, indice: dict, rids: list[RID]) -> list[int]:
        if not indice["por_docid"]:
            rutas = vectorsearch.rutas_indexadas(str(len(indice["por_ruta"])))
            indice["por_docid"] = {
                rid: docid
                for docid, ruta in enumerate(rutas)
                if (rid := indice["por_ruta"].get(_normalizar(ruta))) is not None
            }
        return [docid for rid in rids if (docid := indice["por_docid"].get(rid)) is not None]

    def _entrada(self, name: str) -> dict:
        try:
            return self._indices[name.lower()]
        except KeyError:
            raise StorageUnavailableError(
                f"el indice vectorial '{name}' no esta abierto"
            ) from None


def _normalizar(valor) -> str:
    if valor is None:
        return ""
    return str(valor).strip().replace("\\", "/").lstrip("./")


def _telemetria(respuesta: dict) -> dict:
    return {
        "candidates": int(respuesta.get("candidates", 0)),
        "collection_size": int(respuesta.get("collection_size", 0)),
        "selectivity": float(respuesta.get("selectivity", 0.0)),
        "search_ms": float(respuesta.get("search_ms", 0.0)),
    }
