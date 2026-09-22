from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

from extendible_hash import ExtendibleHashFile
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from storage import DEFAULT_PAGE_SIZE, DiskCounter, build_storage_backend

app = FastAPI(
    title="Dynamic Hashing Index Service",
    description="Microservicio del indice de Hashing Dinamico - CS2042 BD II, Bloque 04",
    version="1.0.0",
)

#por defecto guarda todo local, se puede cambiar a "remote" cuando este listo el storage
STORAGE_MODE = os.environ.get("STORAGE_MODE", "embedded")
STORAGE_BASE_URL = os.environ.get("STORAGE_BASE_URL", "http://storage-service:8000")
DATA_DIR = os.environ.get("HASH_DATA_DIR", "./data")
CATALOG_PATH = os.path.join(DATA_DIR, "catalog.json")

os.makedirs(DATA_DIR, exist_ok=True)

#un solo contador para todo el servicio, para que las metricas de i/o no se pisen entre indices
SHARED_COUNTER = DiskCounter()

_open_indexes: Dict[str, ExtendibleHashFile] = {}


def _load_catalog() -> dict:
    if not os.path.exists(CATALOG_PATH):
        return {}
    with open(CATALOG_PATH, "r") as f:
        return json.load(f)


def _save_catalog(catalog: dict) -> None:
    with open(CATALOG_PATH, "w") as f:
        json.dump(catalog, f, indent=2)


def _get_index(index_name: str) -> ExtendibleHashFile:
    if index_name in _open_indexes:
        return _open_indexes[index_name]

    catalog = _load_catalog()
    if index_name not in catalog:
        raise HTTPException(
            status_code=404,
            detail=f"El indice '{index_name}' no existe. Creala primero con POST /indexes.",
        )

    meta = catalog[index_name]
    storage = build_storage_backend(
        mode=STORAGE_MODE,
        db_path=os.path.join(DATA_DIR, f"{index_name}.hash.bin"),
        base_url=STORAGE_BASE_URL,
        page_size=meta["block_size"],
        counter=SHARED_COUNTER,
    )
    idx = ExtendibleHashFile.open(storage, meta["header_page_id"])
    _open_indexes[index_name] = idx
    return idx


#estos son los formatos de lo que entra y sale de cada endpoint

class CreateIndexRequest(BaseModel):
    index_name: str
    block_size: int = DEFAULT_PAGE_SIZE


class CreateIndexResponse(BaseModel):
    index_name: str
    header_page_id: int
    block_size: int


class InsertRequest(BaseModel):
    key: int
    rid: Tuple[int, int]


class InsertResponse(BaseModel):
    index_name: str
    key: int
    inserted: bool


class SearchResponse(BaseModel):
    key: int
    rids: List[Tuple[int, int]]


class DeleteResponse(BaseModel):
    key: int
    removed: bool


class StatsResponse(BaseModel):
    index_name: str
    global_depth: int
    directory_entries: int
    distinct_buckets: int
    total_entries: int
    bucket_capacity: int
    block_size: int
    disk_reads: int
    disk_writes: int


@app.get("/")
def root():
    return {
        "service": "Dynamic Hashing Index Service",
        "algorithm": "extendible_hashing",
        "storage_mode": STORAGE_MODE,
        "status": "online",
    }


@app.post("/indexes", response_model=CreateIndexResponse)
def create_index(req: CreateIndexRequest):
    #esto es lo mismo que hacer CREATE INDEX ... USING HASH
    catalog = _load_catalog()
    if req.index_name in catalog:
        raise HTTPException(status_code=409, detail=f"El indice '{req.index_name}' ya existe.")

    storage = build_storage_backend(
        mode=STORAGE_MODE,
        db_path=os.path.join(DATA_DIR, f"{req.index_name}.hash.bin"),
        base_url=STORAGE_BASE_URL,
        page_size=req.block_size,
        counter=SHARED_COUNTER,
    )
    idx = ExtendibleHashFile.create(storage)
    _open_indexes[req.index_name] = idx

    catalog[req.index_name] = {"header_page_id": idx.header_page_id, "block_size": req.block_size}
    _save_catalog(catalog)

    return CreateIndexResponse(
        index_name=req.index_name, header_page_id=idx.header_page_id, block_size=req.block_size
    )


@app.post("/indexes/{index_name}/insert", response_model=InsertResponse)
def insert(index_name: str, req: InsertRequest):
    idx = _get_index(index_name)
    idx.insert(req.key, tuple(req.rid))
    return InsertResponse(index_name=index_name, key=req.key, inserted=True)


@app.get("/indexes/{index_name}/search", response_model=SearchResponse)
def search(index_name: str, key: int = Query(..., description="Busqueda exacta: WHERE columna = key")):
    idx = _get_index(index_name)
    return SearchResponse(key=key, rids=idx.search(key))


@app.delete("/indexes/{index_name}", response_model=DeleteResponse)
def delete(
    index_name: str,
    key: int = Query(...),
    rid_page: Optional[int] = Query(None, description="Opcional: rid exacto si la clave tiene duplicados"),
    rid_slot: Optional[int] = Query(None),
):
    idx = _get_index(index_name)
    rid = (rid_page, rid_slot) if rid_page is not None and rid_slot is not None else None
    removed = idx.delete(key, rid)
    return DeleteResponse(key=key, removed=removed)


@app.get("/indexes/{index_name}/stats", response_model=StatsResponse)
def stats(index_name: str):
    idx = _get_index(index_name)
    return StatsResponse(index_name=index_name, **idx.stats())


@app.get("/telemetry/metrics")
def telemetry():
    #esto es la telemetria de i/o que pide el profe
    return SHARED_COUNTER.get_metrics()


@app.post("/telemetry/reset")
def reset_telemetry():
    #esto reinicia el contador para empezar un experimento limpio
    SHARED_COUNTER.reset()
    return {"status": "reset_successful", "metrics": SHARED_COUNTER.get_metrics()}
