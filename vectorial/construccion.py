"""Bloque de construcción de índices: extrae características y escribe todo en disco.

El build guarda su avance (checkpoint) cada CHECKPOINT_CADA_S segundos y al cortar con Ctrl+C.
Si lo vuelves a lanzar con el MISMO comando, continúa donde quedó.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np
from sklearn.cluster import MiniBatchKMeans

from bovw import SiftBoVW, tfidf, train_visual_words
from config import (CHECKPOINT_CADA_S, CLUSTERS_IVF, DESCRIPTORES_POR_IMAGEN, DIM_EMBEDDING,
                    IMAGENES_ENTRENAMIENTO, LADO_MAX, LIMITE_BUILD, NFEATURES_SIFT, PALABRAS_VISUALES,
                    SEMILLA)
from imagenes import list_images, read_image
from modelos import FaceEmbedder
from postings import save_postings


# ---------------------------------------------------------------------------
# Bloque de checkpoints (un solo archivo .npz, escrito de forma atómica)
# ---------------------------------------------------------------------------
def _guardar_checkpoint(carpeta: Path, firma: dict, hechas: int, names, histograms, embeddings, valid) -> None:
    carpeta.mkdir(parents=True, exist_ok=True)
    tmp = carpeta / "estado.tmp.npz"
    np.savez(tmp,
             firma=np.array(json.dumps(firma, sort_keys=True)),
             pos=np.array(hechas),
             names=np.array(names, dtype=str),
             hist=np.stack(histograms) if histograms else np.empty((0, 0), np.float32),
             emb=np.stack(embeddings) if embeddings else np.empty((0, DIM_EMBEDDING), np.float32),
             valid=np.asarray(valid, dtype=np.bool_))
    tmp.replace(carpeta / "estado.npz")


def _cargar_checkpoint(carpeta: Path, firma: dict):
    """Devuelve (hechas, names, histograms, embeddings, valid) o None si no hay uno compatible."""
    archivo = carpeta / "estado.npz"
    if not archivo.exists():
        return None
    try:
        with np.load(archivo, allow_pickle=False) as z:
            if str(z["firma"]) != json.dumps(firma, sort_keys=True):
                print("Checkpoint de otra configuración: se ignora y se empieza desde cero.")
                return None
            return (int(z["pos"]), [str(x) for x in z["names"]], list(z["hist"]),
                    list(z["emb"]), z["valid"].tolist())
    except Exception as e:  # noqa: BLE001 - archivo dañado: se empieza de cero
        print(f"Checkpoint ilegible ({e}): se empieza desde cero.")
        return None


def build(data_root: Path, out: Path, *, method: str = "all", limit: int = LIMITE_BUILD,
          words: int = PALABRAS_VISUALES, clusters: int = CLUSTERS_IVF,
          train_images: int = IMAGENES_ENTRENAMIENTO, per_image: int = DESCRIPTORES_POR_IMAGEN,
          max_side: int = LADO_MAX, nfeatures: int = NFEATURES_SIFT,
          checkpoint_cada_s: float = CHECKPOINT_CADA_S) -> dict:
    # -----------------------------------------------------------------------
    # Bloque de validación de parámetros y listado del dataset
    # -----------------------------------------------------------------------
    data_root = data_root.resolve()
    out = out.resolve()
    paths = [p for p in list_images(data_root, limit=0) if not p.is_relative_to(out)]
    if limit > 0:
        paths = paths[:limit]
    if not paths:
        raise ValueError("El dataset está vacío o está dentro del directorio del índice.")
    if method not in {"face", "bovw", "all"}:
        raise ValueError("method debe ser face, bovw o all")
    if min(words, clusters, train_images, per_image, max_side, nfeatures) < 1:
        raise ValueError("Los parámetros de entrenamiento deben ser positivos")
    out.mkdir(parents=True, exist_ok=True)
    use_face = method in {"face", "all"}
    use_bovw = method in {"bovw", "all"}
    print(f"Dataset: {len(paths)} imágenes. Método: {method}.")

    # -----------------------------------------------------------------------
    # Bloque para decidir si se reanuda desde un checkpoint
    # -----------------------------------------------------------------------
    dir_ckpt = out / "checkpoint"
    firma = {"root": str(data_root), "method": method, "limit": limit, "words": words,
             "train_images": train_images, "per_image": per_image, "max_side": max_side,
             "nfeatures": nfeatures, "n_paths": len(paths),
             "first": paths[0].relative_to(data_root).as_posix(),
             "last": paths[-1].relative_to(data_root).as_posix()}
    estado = _cargar_checkpoint(dir_ckpt, firma)
    if estado and use_bovw and not (out / "codebook.npy").exists():
        estado = None
    if estado is None:
        shutil.rmtree(dir_ckpt, ignore_errors=True)

    # -----------------------------------------------------------------------
    # Bloque de preparación: vocabulario visual y modelos faciales
    # -----------------------------------------------------------------------
    words_model = None
    sift = None
    embedder = None
    if use_bovw:
        if estado:
            codebook = np.load(out / "codebook.npy")
            print("[1/3] Vocabulario visual recuperado del checkpoint.")
        else:
            print("[1/3] Entrenando vocabulario visual SIFT + MiniBatchKMeans...")
            codebook = train_visual_words(paths, words, train_images, per_image, max_side, nfeatures)
            np.save(out / "codebook.npy", codebook)
        sift = SiftBoVW(codebook, nfeatures)
        words_model = len(codebook)
    if use_face:
        print("[2/3] Cargando modelos YuNet + SFace...")
        embedder = FaceEmbedder(out / "models")

    # -----------------------------------------------------------------------
    # Bloque de extracción de características por imagen (con checkpoints)
    # -----------------------------------------------------------------------
    names, histograms, embeddings, valid = [], [], [], []
    inicio = 0
    if estado:
        inicio, names, histograms, embeddings, valid = estado
        print(f"[3/3] Reanudando desde el checkpoint: {inicio}/{len(paths)} imágenes ya procesadas.")
    else:
        print("[3/3] Extrayendo características...")
    hechas = inicio
    ultimo_ckpt = time.monotonic()
    try:
        for pos in range(inicio, len(paths)):
            path = paths[pos]
            img = read_image(path, max_side)
            if img is None:
                print(f"  Omitida (no se pudo leer): {path}")
            else:
                hist = sift.histogram(img) if use_bovw else None
                vec = embedder.extract(img) if use_face else None
                names.append(path.relative_to(data_root).as_posix())
                if use_bovw:
                    histograms.append(hist)
                if use_face:
                    valid.append(vec is not None)
                    embeddings.append(vec if vec is not None else np.zeros(DIM_EMBEDDING, np.float32))
                if len(names) % 100 == 0:
                    print(f"  Procesadas: {len(names)}/{len(paths)}")
            hechas = pos + 1
            if time.monotonic() - ultimo_ckpt >= checkpoint_cada_s and hechas < len(paths):
                _guardar_checkpoint(dir_ckpt, firma, hechas, names, histograms, embeddings, valid)
                ultimo_ckpt = time.monotonic()
                print(f"  [checkpoint guardado: {hechas}/{len(paths)}]")
    except KeyboardInterrupt:
        _guardar_checkpoint(dir_ckpt, firma, hechas, names, histograms, embeddings, valid)
        print(f"\nInterrumpido. Checkpoint guardado ({hechas}/{len(paths)}). "
              "Vuelve a lanzar el mismo comando para continuar.")
        raise
    n = len(names)
    if n == 0:
        raise RuntimeError("No se pudieron leer imágenes válidas.")

    # -----------------------------------------------------------------------
    # Bloque del índice invertido BoVW (IDF, vectores TF-IDF y posting lists)
    # -----------------------------------------------------------------------
    if use_bovw:
        hist = np.stack(histograms).astype(np.float32)
        df = np.count_nonzero(hist, axis=0)
        idf = (np.log((1.0 + n) / (1.0 + df)) + 1).astype(np.float32)
        np.save(out / "idf.npy", idf)
        np.save(out / "bovw.npy", tfidf(hist, idf))
        save_postings(out, "bovw", [np.flatnonzero(hist[:, j] > 0) for j in range(words_model)])

    # -----------------------------------------------------------------------
    # Bloque del índice IVF facial (K-Means sobre embeddings + posting lists)
    # -----------------------------------------------------------------------
    n_face = 0
    actual_clusters = 0
    if use_face:
        faces = np.stack(embeddings).astype(np.float32)
        np.save(out / "faces.npy", faces)
        mask = np.asarray(valid, dtype=np.bool_)
        np.save(out / "face_valid.npy", mask)
        ids = np.flatnonzero(mask)
        n_face = len(ids)
        if n_face:
            actual_clusters = min(clusters, n_face)
            if actual_clusters == 1:
                centroids = faces[ids[:1]]
                assignment = np.zeros(n_face, np.int32)
            else:
                km = MiniBatchKMeans(n_clusters=actual_clusters, random_state=SEMILLA,
                                     batch_size=max(256, actual_clusters * 3), n_init=3,
                                     max_iter=100)
                assignment = km.fit_predict(faces[ids])
                centroids = km.cluster_centers_.astype(np.float32)
            np.save(out / "face_centroids.npy", centroids)
            save_postings(out, "face", [ids[assignment == j] for j in range(actual_clusters)])
        else:
            print("AVISO: ninguna imagen produjo rostro. El índice facial queda vacío.")
            np.save(out / "face_centroids.npy", np.empty((0, DIM_EMBEDDING), np.float32))
            save_postings(out, "face", [])

    # -----------------------------------------------------------------------
    # Bloque del manifiesto (se guarda al final: si existe, la construcción terminó)
    # -----------------------------------------------------------------------
    metadata = {"version": 1, "root": str(data_root), "paths": names, "count": n,
                "method": method, "n_face": n_face, "words": words_model,
                "face_clusters": actual_clusters, "max_side": max_side,
                "nfeatures": nfeatures, "created_unix": time.time()}
    temp = out / "metadata.tmp"
    temp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(out / "metadata.json")
    shutil.rmtree(dir_ckpt, ignore_errors=True)  # build terminado: el checkpoint ya no sirve
    print(f"Índice listo en {out}: {n} imágenes, {n_face} rostros, {words_model or 0} palabras.")
    return metadata
