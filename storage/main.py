import base64
from typing import Optional
from disk_management import DiskManager
from fastapi import FastAPI, HTTPException
from page import Page
from pydantic import BaseModel
from bplus_tree import BPlusTree

app = FastAPI(
    title="Physical Storage Engine API",
    description=(
        "Microservicio de Almacenamiento Físico y Telemetría de E/S"
        " (CS2042 - BD II)"
    ),
    version="1.0.0",
)

# Instancia global de DiskManager apuntando a la base de datos binaria
DB_FILENAME = "engine_data.bin"
dm_heap = DiskManager(DB_FILENAME)

# B+ Gestor para el índice
IDX_FILENAME = "engine_index.idx"
dm_index = DiskManager(IDX_FILENAME)
bptree = BPlusTree(dm_index)


# Modelos de entrada / salida
class AllocateResponse(BaseModel):
  page_id: int
  disk_writes: int


class RecordInsertRequest(BaseModel):
  page_id: int
  # Enviamos los bytes codificados en base64 para admitir cualquier struct binario
  record_base64: str


class RecordInsertResponse(BaseModel):
  page_id: int
  slot_number: int
  rid: tuple[int, int]
  disk_writes: int


class PageHeaderResponse(BaseModel):
  page_id: int
  record_count: int
  free_space_offset: int
  next_page_id: int
  prev_page_id: int


# --- [NUEVO] MODELO PARA EL ÍNDICE ---
class IndexInsertRequest(BaseModel):
  key: int
  page_id: int
  slot_number: int


@app.get("/")
def root():
  return {
      "service": "Physical Storage Engine",
      "status": "online",
      "page_size": 4096,
  }


@app.post("/pages/allocate", response_model=AllocateResponse)
def allocate_page():
  """Reserva una nueva página vacía de 4096 bytes en disco."""
  new_page_id = dm_heap.allocate_page()
  # Inicializa la cabecera básica de la página
  page = Page(page_id=new_page_id)
  dm_heap.write_page(new_page_id, page.to_bytes())

  metrics = dm_heap.counter.get_metrics()
  return AllocateResponse(
      page_id=new_page_id, disk_writes=metrics["disk_writes"]
  )


@app.get("/pages/{page_id}/header", response_model=PageHeaderResponse)
def get_page_header(page_id: int):
  """Lee el bloque y retorna únicamente los metadatos de su cabecera."""
  try:
    raw_block = dm_heap.read_page(page_id)
  except IndexError:
    raise HTTPException(status_code=404, detail="Página no encontrada")

  page = Page(page_id=page_id, raw_bytes=raw_block)
  return PageHeaderResponse(
      page_id=page.page_id,
      record_count=page.record_count,
      free_space_offset=page.free_space_offset,
      next_page_id=page.next_page_id,
      prev_page_id=page.prev_page_id,
  )


@app.post("/records", response_model=RecordInsertResponse)
def insert_record(req: RecordInsertRequest):
  """Inserta los bytes de una tupla en el bloque indicado y devuelve su RID."""
  try:
    raw_block = dm_heap.read_page(req.page_id)
  except IndexError:
    raise HTTPException(status_code=404, detail="Página no encontrada")

  page = Page(page_id=req.page_id, raw_bytes=raw_block)
  record_bytes = base64.b64decode(req.record_base64)

  slot_number = page.insert_record(record_bytes)
  if slot_number is None:
    raise HTTPException(
        status_code=400,
        detail=(
            f"Página {req.page_id} sin espacio suficiente para este registro"
        ),
    )

  # Persistir los cambios en disco
  dm_heap.write_page(req.page_id, page.to_bytes())

  metrics = dm_heap.counter.get_metrics()
  return RecordInsertResponse(
      page_id=req.page_id,
      slot_number=slot_number,
      rid=(req.page_id, slot_number),
      disk_writes=metrics["disk_writes"],
  )


@app.get("/records/{page_id}/{slot_number}")
def get_record(page_id: int, slot_number: int):
  """Recupera los bytes exactos de una tupla mediante su RID."""
  try:
    raw_block = dm_heap.read_page(page_id)
  except IndexError:
    raise HTTPException(status_code=404, detail="Página no encontrada")

  page = Page(page_id=page_id, raw_bytes=raw_block)
  rec_bytes = page.get_record(slot_number)

  if rec_bytes is None:
    raise HTTPException(
        status_code=404, detail="Registro o slot no encontrado/eliminado"
    )

  return {
      "page_id": page_id,
      "slot_number": slot_number,
      "record_base64": base64.b64encode(rec_bytes).decode("ascii"),
  }


# ==========================================
# [NUEVO] ENDPOINTS PARA EL ÁRBOL B+
# ==========================================

@app.post("/index/insert")
def insert_index_entry(req: IndexInsertRequest):
  """Inserta una llave y su RID (page_id, slot) en el Árbol B+."""
  # Insertamos pasando la llave y una tupla que representa el RID
  bptree.insert(req.key, (req.page_id, req.slot_number))
  
  metrics = dm_index.counter.get_metrics()
  return {
      "message": "Index entry inserted successfully",
      "key": req.key,
      "index_disk_writes": metrics["disk_writes"]
  }

@app.get("/index/search/{key}")
def search_index(key: int):
  """Busca una llave en el Árbol B+ y retorna su RID asociado."""
  # Reiniciamos el contador opcionalmente si deseas medir lecturas exactas por consulta
  dm_index.counter.reset() 
  
  rid = bptree.search(key)
  
  if rid is None:
      raise HTTPException(status_code=404, detail="Key no encontrada en el índice")
      
  metrics = dm_index.counter.get_metrics()
  return {
      "key": key,
      "rid": {"page_id": rid[0], "slot_number": rid[1]},
      "index_disk_reads": metrics["disk_reads"]
  }

@app.get("/index/search_range/")
def search_index_range(start_key: int, end_key: int):
  """Busca un rango de llaves en el Árbol B+ y retorna una lista de RIDs."""
  # Reiniciamos para contar cuántas páginas lee exactamente este escaneo de rango
  dm_index.counter.reset() 
  
  results = bptree.rangeSearch(start_key, end_key)
  
  metrics = dm_index.counter.get_metrics()
  return {
      "start_key": start_key,
      "end_key": end_key,
      "results_count": len(results),
      # Formateamos la lista de tuplas (page, slot) a un formato JSON amigable
      "rids": [{"page_id": r[0], "slot_number": r[1]} for r in results],
      "index_disk_reads": metrics["disk_reads"]
  }


# ==========================================
# ACTUALIZACIÓN DE TELEMETRÍA
# ==========================================

@app.get("/telemetry/metrics")
def get_telemetry():
  """Retorna la telemetría obligatoria de I/O, separada por archivo."""
  heap_metrics = dm_heap.counter.get_metrics()
  index_metrics = dm_index.counter.get_metrics()
  
  return {
      "heap_file": heap_metrics,
      "bplus_tree": index_metrics,
      "total": {
          "disk_reads": heap_metrics["disk_reads"] + index_metrics["disk_reads"],
          "disk_writes": heap_metrics["disk_writes"] + index_metrics["disk_writes"]
      }
  }


@app.post("/telemetry/reset")
def reset_telemetry():
  """Reinicia los contadores de disco para iniciar un nuevo experimento."""
  dm_heap.counter.reset()
  dm_index.counter.reset()
  
  return {
      "status": "reset_successful", 
      "metrics": {
          "heap_file": dm_heap.counter.get_metrics(),
          "bplus_tree": dm_index.counter.get_metrics()
      }
  }
