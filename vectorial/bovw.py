"""Bloque de SIFT + Bag of Visual Words: vocabulario, histogramas y ponderación TF-IDF."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from sklearn.cluster import MiniBatchKMeans

from config import NFEATURES_SIFT, SEMILLA
from imagenes import read_image


# ---------------------------------------------------------------------------
# Bloque para asignar descriptores a la palabra visual más cercana
# ---------------------------------------------------------------------------
def nearest_centers(x: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """Distancia L2, en lotes para limitar uso de RAM."""
    result = np.empty(len(x), dtype=np.int32)
    centers = np.asarray(centers, np.float32)
    c2 = (centers * centers).sum(axis=1)
    for start in range(0, len(x), 512):
        batch = x[start:start + 512]
        d2 = (batch * batch).sum(axis=1, keepdims=True) + c2[None, :] - 2 * batch @ centers.T
        result[start:start + len(batch)] = np.argmin(d2, axis=1)
    return result


# ---------------------------------------------------------------------------
# Bloque del extractor SIFT -> histograma de palabras visuales
# ---------------------------------------------------------------------------
class SiftBoVW:
    def __init__(self, codebook: np.ndarray | None = None, nfeatures: int = NFEATURES_SIFT):
        self.sift = cv2.SIFT_create(nfeatures=nfeatures)
        self.codebook = codebook

    def descriptors(self, img: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, desc = self.sift.detectAndCompute(gray, None)
        if desc is None:
            return np.empty((0, 128), dtype=np.float32)
        return desc.astype(np.float32, copy=False)

    def histogram(self, img: np.ndarray) -> np.ndarray:
        if self.codebook is None:
            raise RuntimeError("Falta entrenar el vocabulario visual.")
        desc = self.descriptors(img)
        if len(desc) == 0:
            return np.zeros(len(self.codebook), np.float32)
        # Asigna cada descriptor SIFT a la palabra visual más cercana.
        assignments = nearest_centers(desc, self.codebook)
        return np.bincount(assignments, minlength=len(self.codebook)).astype(np.float32)


# ---------------------------------------------------------------------------
# Bloque para entrenar el vocabulario (K-Means sobre descriptores SIFT)
# ---------------------------------------------------------------------------
def train_visual_words(paths: list[Path], words: int, train_images: int, per_image: int,
                       max_side: int, nfeatures: int, seed: int = SEMILLA) -> np.ndarray:
    rng = np.random.default_rng(seed)
    picker = SiftBoVW(nfeatures=nfeatures)
    selected = rng.choice(len(paths), size=min(train_images, len(paths)), replace=False)
    samples = []
    for i, idx in enumerate(selected, 1):
        img = read_image(paths[int(idx)], max_side)
        if img is None:
            continue
        descriptors = picker.descriptors(img)
        if len(descriptors):
            chosen = rng.choice(len(descriptors), min(per_image, len(descriptors)), replace=False)
            samples.append(descriptors[chosen])
        if i % 100 == 0:
            print(f"  Vocabulario SIFT: {i}/{len(selected)} imágenes")
    if not samples:
        raise ValueError("No se encontraron descriptores SIFT. Comprueba las imágenes del dataset.")
    x = np.vstack(samples)
    k = min(words, len(x))
    if k == 1:
        return x[:1].copy()
    kmeans = MiniBatchKMeans(n_clusters=k, random_state=seed, batch_size=max(256, k * 3),
                             n_init=3, max_iter=100)
    kmeans.fit(x)
    return kmeans.cluster_centers_.astype(np.float32)


# ---------------------------------------------------------------------------
# Bloque de ponderación TF-IDF
# ---------------------------------------------------------------------------
def tfidf(hist: np.ndarray, idf: np.ndarray) -> np.ndarray:
    """TF normalizado por la suma, multiplicado por IDF y normalizado L2."""
    hist = np.asarray(hist, np.float32)
    sums = hist.sum(axis=-1, keepdims=True)
    tf = np.divide(hist, sums, out=np.zeros_like(hist), where=sums > 0)
    scores = tf * idf
    norms = np.linalg.norm(scores, axis=-1, keepdims=True)
    return np.divide(scores, norms, out=np.zeros_like(scores), where=norms > 1e-10)
