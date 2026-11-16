"""Bloque de API HTTP del Bloque C (FastAPI). Es el contrato que usa el planificador de Nicolás.

Endpoints:
  GET  /health            -> {"ok": true}
  GET  /api/status        -> resumen del índice
  POST /api/search        -> multipart: file, mode(face|bovw), k, nprobe, query_words, max_candidates
  POST /api/embed         -> multipart: file, mode -> el vector de consulta
  POST /api/search_vector -> json: vector, mode, k, nprobe -> Top-K (post-filtering)
  POST /api/score         -> json: vector, mode, ids -> similitud de esos ids (pre-filtering)
  GET  /api/image/{id}    -> la imagen indexada con ese id
  GET  /api/image_by_path -> la misma imagen, buscada por su ruta relativa
  GET  /api/paths         -> docid -> ruta relativa, para que el motor mapee sus RIDs
"""
import time

import cv2
import numpy as np
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
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
                "dataset_available": idx.root.is_dir(),
                "face_clusters": int(idx.meta.get("face_clusters", 0)),
                "words": int(idx.meta.get("words", 0)),
                "k_max": K_MAXIMO}

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

    # -----------------------------------------------------------------------
    # Bloque de contrato con el motor de consultas (planificador híbrido)
    # -----------------------------------------------------------------------
    def _decode(data: bytes) -> np.ndarray:
        if len(data) > MAX_BYTES_CONSULTA:
            raise HTTPException(400, f"La imagen no puede superar {MAX_BYTES_CONSULTA // (1024 * 1024)} MB")
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(400, "No se pudo decodificar la imagen")
        return image

    @api.post("/api/embed")
    async def embed(file: UploadFile = File(...), mode: str = Form("face")):
        # El vector de consulta, para que el motor lo ponga en el SQL como literal.
        image = _decode(await file.read(MAX_BYTES_CONSULTA + 1))
        try:
            vector = idx.query_features(image, mode)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e)) from e
        return {"mode": mode, "dim": int(vector.shape[0]),
                "vector": [round(float(v), 6) for v in vector]}

    @api.post("/api/search_vector")
    def search_vector(payload: dict = Body(...)):
        # Post-filtering: el motor manda el vector y recibe los candidatos del IVF.
        vector = np.asarray(payload.get("vector", []), dtype=np.float32)
        if not len(vector):
            raise HTTPException(400, "falta el vector de consulta")
        try:
            return idx.search_vector(
                vector, payload.get("mode", "face"),
                k=min(max(1, int(payload.get("k", 5))), K_MAXIMO),
                nprobe=int(payload.get("nprobe", NPROBE)),
                query_words=int(payload.get("query_words", PALABRAS_CONSULTA)),
                max_candidates=int(payload.get("max_candidates", MAX_CANDIDATOS)),
            )
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e)) from e

    @api.post("/api/score")
    def score(payload: dict = Body(...)):
        # Pre-filtering: el motor ya filtró por el índice relacional y manda sus ids.
        mode = payload.get("mode", "face")
        ids = np.asarray(payload.get("ids", []), dtype=np.int32)
        vector = np.asarray(payload.get("vector", []), dtype=np.float32)
        if not len(vector):
            raise HTTPException(400, "falta el vector de consulta")
        start = time.perf_counter()
        valores = idx._score(vector, mode, ids)
        return {"mode": mode,
                "scores": [{"id": int(i), "similarity": round(float(s), 5)}
                           for i, s in zip(ids.tolist(), valores.tolist())],
                "candidates": int(len(ids)),
                "search_ms": round((time.perf_counter() - start) * 1000, 4)}

    @api.get("/api/image_by_path")
    def image_by_path(path: str):
        # El motor trabaja con rutas, no con docid: esto cierra el circulo para la UI.
        objetivo = path.replace("\\", "/").strip("/")
        destino = (idx.root / objetivo).resolve()
        if not str(destino).startswith(str(idx.root.resolve())) or not destino.is_file():
            raise HTTPException(404, f"no se encontro {path}")
        return FileResponse(destino)

    @api.get("/api/paths")
    def paths(offset: int = 0, limit: int = 5000):
        # El motor construye con esto su mapa ruta -> RID al crear el índice.
        total = int(idx.meta["count"])
        fin = min(total, max(0, offset) + max(1, limit))
        return {"total": total, "offset": offset,
                "paths": [{"id": i, "path": idx.paths[i]} for i in range(offset, fin)]}

    return api


def serve(idx: VectorIndex, host: str = HOST, port: int = PUERTO) -> None:
    import uvicorn
    print(f"Web: http://{host}:{port}")
    uvicorn.run(create_app(idx), host=host, port=port)
