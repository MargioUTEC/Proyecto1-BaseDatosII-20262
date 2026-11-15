"""Bloque de consulta: VectorIndex abre los índices en disco y hace Filter-and-Refine.

Filter: se obtienen candidatos desde las posting lists (IVF facial o palabras BoVW).
Refine: los candidatos se reordenan con la similitud coseno real.
"""
from __future__ import annotations

import heapq
import json
import time
from pathlib import Path

import numpy as np

from bovw import SiftBoVW, tfidf
from config import MAX_CANDIDATOS, NPROBE, PALABRAS_CONSULTA
from imagenes import prepare_image
from modelos import FaceEmbedder
from postings import open_postings, read_list


class VectorIndex:
    """Consulta índices de disco sin reconstruirlos; matrices NumPy con memmap."""

    # -----------------------------------------------------------------------
    # Bloque de apertura del índice
    # -----------------------------------------------------------------------
    def __init__(self, folder: Path, root_override: Path | None = None):
        self.folder = folder.resolve()
        meta_path = self.folder / "metadata.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"No existe índice en {self.folder}. Ejecuta 'build' primero.")
        self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.root = Path(root_override).resolve() if root_override else Path(self.meta["root"])
        self.paths = self.meta["paths"]
        self.face = self.meta["method"] in {"all", "face"}
        self.bovw = self.meta["method"] in {"all", "bovw"}
        self._embedder = None
        if self.face:
            self.faces = np.load(self.folder / "faces.npy", mmap_mode="r")
            self.face_valid = np.load(self.folder / "face_valid.npy", mmap_mode="r")
            self.centroids = np.load(self.folder / "face_centroids.npy", mmap_mode="r")
            self.face_offsets, self.face_posts = open_postings(self.folder, "face", empty=not self.meta["n_face"])
        if self.bovw:
            self.bovw_vectors = np.load(self.folder / "bovw.npy", mmap_mode="r")
            self.idf = np.load(self.folder / "idf.npy", mmap_mode="r")
            self.sift = SiftBoVW(np.load(self.folder / "codebook.npy"), self.meta["nfeatures"])
            self.bovw_offsets, self.bovw_posts = open_postings(self.folder, "bovw")

    def path(self, docid: int) -> Path:
        return self.root / self.paths[int(docid)]

    def _list(self, stem: str, cluster: int) -> np.ndarray:
        if stem == "face":
            return read_list(self.face_offsets, self.face_posts, cluster)
        return read_list(self.bovw_offsets, self.bovw_posts, cluster)

    # -----------------------------------------------------------------------
    # Bloque para convertir una imagen de consulta en vector
    # -----------------------------------------------------------------------
    def embedding(self, image: np.ndarray) -> np.ndarray | None:
        if self._embedder is None:
            self._embedder = FaceEmbedder(self.folder / "models")
        return self._embedder.extract(image)

    def query_features(self, image: np.ndarray, mode: str) -> np.ndarray:
        image = prepare_image(image, self.meta["max_side"])
        if image is None:
            raise ValueError("Imagen de consulta inválida.")
        if mode == "face":
            if not self.face:
                raise ValueError("Este índice no contiene embeddings faciales.")
            if not self.meta["n_face"]:
                raise ValueError("No existen rostros detectados en el índice.")
            vector = self.embedding(image)
            if vector is None:
                raise ValueError("No se detectó un rostro en la imagen de consulta.")
            return vector
        if mode == "bovw":
            if not self.bovw:
                raise ValueError("Este índice no contiene descriptores SIFT/BoVW.")
            hist = self.sift.histogram(image)
            if hist.sum() == 0:
                raise ValueError("La imagen de consulta no tiene puntos SIFT detectables.")
            return tfidf(hist, self.idf)
        raise ValueError("Modo debe ser 'face' o 'bovw'.")

    # -----------------------------------------------------------------------
    # Bloque FILTER: candidatos desde las posting lists
    # -----------------------------------------------------------------------
    def candidates(self, vector: np.ndarray, mode: str, *, nprobe: int = NPROBE,
                   query_words: int = PALABRAS_CONSULTA, max_candidates: int = MAX_CANDIDATOS) -> np.ndarray:
        if mode == "face":
            # IVF: se abren solo las listas de los nprobe centroides más cercanos.
            if len(self.centroids) == 0:
                return np.array([], dtype=np.int32)
            distances = np.sum((self.centroids - vector) ** 2, axis=1)
            nearest = np.argsort(distances)[:max(1, min(nprobe, len(distances)))]
            return np.concatenate([self._list("face", int(c)) for c in nearest]).astype(np.int32)
        # BoVW: filtrar por las palabras más discriminativas de la consulta (TF-IDF).
        # El ranking final se computa con todo el vector, no solo esas palabras.
        words = np.flatnonzero(vector > 0)
        if not len(words):
            return np.array([], np.int32)
        chosen = sorted(words, key=lambda i: (-float(vector[i]),
                                              int(self.bovw_offsets[i + 1] - self.bovw_offsets[i])))[:max(1, query_words)]
        scores = {}
        for word in chosen:
            for docid in self._list("bovw", int(word)):
                idx = int(docid)
                scores[idx] = scores.get(idx, 0.0) + float(vector[word])
        if not scores:
            return np.array([], dtype=np.int32)
        # Limitar candidatos antes del refinamiento; evita evaluar toda la colección.
        best = heapq.nlargest(max(1, max_candidates), scores, key=scores.get)
        return np.asarray(best, dtype=np.int32)

    # -----------------------------------------------------------------------
    # Bloque REFINE: coseno real sobre los candidatos y Top-K
    # -----------------------------------------------------------------------
    def _score(self, vector: np.ndarray, mode: str, ids: np.ndarray) -> np.ndarray:
        data = self.faces if mode == "face" else self.bovw_vectors
        if not len(ids):
            return np.array([], np.float32)
        return np.asarray(data[ids] @ vector, dtype=np.float32)  # coseno, vectores L2-normalizados

    def search_vector(self, vector: np.ndarray, mode: str, k: int = 5, *,
                      nprobe: int = NPROBE, query_words: int = PALABRAS_CONSULTA,
                      max_candidates: int = MAX_CANDIDATOS, exclude_id: int | None = None) -> dict:
        if k < 1:
            raise ValueError("k debe ser >= 1")
        start = time.perf_counter()
        ids = self.candidates(vector, mode, nprobe=nprobe, query_words=query_words,
                              max_candidates=max_candidates)
        if exclude_id is not None:
            ids = ids[ids != exclude_id]
        n_candidates = len(ids)
        scores = self._score(vector, mode, ids)
        top = np.argsort(-scores, kind="stable")[:k]
        rows = [{"id": int(ids[i]), "path": self.paths[int(ids[i])],
                 "similarity": round(float(scores[i]), 5)} for i in top]
        total = int(self.meta["n_face"]) if mode == "face" else int(self.meta["count"])
        return {"mode": mode, "results": rows, "candidates": n_candidates,
                "collection_size": total, "selectivity": round(n_candidates / max(1, total), 6),
                "search_ms": round((time.perf_counter() - start) * 1000, 4)}

    def search_image(self, image: np.ndarray, mode: str, k: int = 5, **kwargs) -> dict:
        start = time.perf_counter()
        feat = self.query_features(image, mode)
        result = self.search_vector(feat, mode, k, **kwargs)
        result["total_ms"] = round((time.perf_counter() - start) * 1000, 4)
        return result

    # -----------------------------------------------------------------------
    # Bloque de búsqueda exhaustiva (línea base para medir recall y tiempo)
    # -----------------------------------------------------------------------
    def exact(self, vector: np.ndarray, mode: str, k: int = 5,
              exclude_id: int | None = None) -> dict:
        start = time.perf_counter()
        ids = np.flatnonzero(self.face_valid).astype(np.int32) if mode == "face" else np.arange(self.meta["count"], dtype=np.int32)
        if exclude_id is not None:
            ids = ids[ids != exclude_id]
        scores = self._score(vector, mode, ids)
        top = np.argsort(-scores, kind="stable")[:k]
        return {"ids": [int(ids[i]) for i in top],
                "search_ms": round((time.perf_counter() - start) * 1000, 4)}
