"""Bloque de API HTTP del Bloque C (FastAPI). Es el contrato que usa el planificador de Nicolás.

Endpoints:
  GET  /health            -> {"ok": true}
  GET  /api/status        -> resumen del índice
  POST /api/search        -> multipart: file, mode(face|bovw), k, nprobe, query_words, max_candidates
  GET  /api/image/{id}    -> la imagen indexada con ese id
"""
import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from config import (DIR_WEB, HOST, K_MAXIMO, MAX_BYTES_CONSULTA, MAX_CANDIDATOS, NPROBE, PALABRAS_CONSULTA,
                    PUERTO)
from indice import VectorIndex


def create_app(idx: VectorIndex) -> FastAPI:
    api = FastAPI(title="BD II - Bloque C - Biométrico y Vectorial")

    # -----------------------------------------------------------------------
    # Bloque de endpoints de estado
    # -----------------------------------------------------------------------
    @api.get("/health")
    def health():
        return {"ok": True}

    @api.get("/")
    def home():
        return FileResponse(DIR_WEB / "index.html", media_type="text/html")

    @api.get("/api/status")
    def status():
        return {"images": idx.meta["count"], "faces": idx.meta["n_face"],
                "method": idx.meta["method"], "root": str(idx.root),
                "dataset_available": idx.root.is_dir()}

    # -----------------------------------------------------------------------
    # Bloque para servir las imágenes indexadas
    # -----------------------------------------------------------------------
    @api.get("/api/image/{docid}")
    def get_image(docid: int):
        if docid < 0 or docid >= idx.meta["count"]:
            raise HTTPException(404, "Imagen no encontrada")
        path = idx.path(docid)
        if not path.is_file():
            raise HTTPException(404, f"No se encontró el dataset en {idx.root}")
        return FileResponse(path)

    # -----------------------------------------------------------------------
    # Bloque de búsqueda por imagen (Filter-and-Refine)
    # -----------------------------------------------------------------------
    @api.post("/api/search")
    async def search(file: UploadFile = File(...), mode: str = Form("face"),
                     k: int = Form(5), nprobe: int = Form(NPROBE),
                     query_words: int = Form(PALABRAS_CONSULTA), max_candidates: int = Form(MAX_CANDIDATOS)):
        data = await file.read(MAX_BYTES_CONSULTA + 1)
        if len(data) > MAX_BYTES_CONSULTA:
            raise HTTPException(400, f"La imagen no puede superar {MAX_BYTES_CONSULTA // (1024 * 1024)} MB")
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(400, "No se pudo decodificar la imagen")
        try:
            return idx.search_image(image, mode, k=min(max(1, k), K_MAXIMO),
                                    nprobe=nprobe, query_words=query_words,
                                    max_candidates=max_candidates)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e)) from e

    return api


def serve(idx: VectorIndex, host: str = HOST, port: int = PUERTO) -> None:
    import uvicorn
    print(f"Web: http://{host}:{port}")
    uvicorn.run(create_app(idx), host=host, port=port)
