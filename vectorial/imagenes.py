"""Bloque de utilidades de imagen: lectura, redimensionado, listado y normalización."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from config import IMAGE_EXT, LADO_MAX


# ---------------------------------------------------------------------------
# Bloque para leer y preparar imágenes
# ---------------------------------------------------------------------------
def read_image(path: Path, max_side: int = LADO_MAX) -> np.ndarray | None:
    """Lee una imagen del disco (funciona con rutas con tildes en Windows)."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    except (OSError, ValueError):
        return None
    return prepare_image(img, max_side)


def prepare_image(img: np.ndarray | None, max_side: int = LADO_MAX) -> np.ndarray | None:
    """Valida que sea BGR de 3 canales y reduce el lado mayor a max_side."""
    if img is None or img.ndim != 3 or img.shape[2] != 3:
        return None
    h, w = img.shape[:2]
    if not h or not w:
        return None
    scale = min(1.0, max_side / max(w, h))
    if scale < 1:
        img = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))),
                         interpolation=cv2.INTER_AREA)
    return img


# ---------------------------------------------------------------------------
# Bloque para listar el dataset
# ---------------------------------------------------------------------------
def list_images(root: Path, limit: int = 0) -> list[Path]:
    """Recorrido recursivo ordenado; limit=0 devuelve todas."""
    if not root.is_dir():
        raise FileNotFoundError(f"Carpeta del dataset no encontrada: {root}")
    paths = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXT)
    if limit > 0:
        paths = paths[:limit]
    if not paths:
        raise ValueError(f"No se encontraron imágenes JPG/PNG/etc. en {root}.")
    return paths


# ---------------------------------------------------------------------------
# Bloque de normalización de vectores
# ---------------------------------------------------------------------------
def unit(x: np.ndarray) -> np.ndarray:
    """Normaliza a norma L2 = 1 (así el producto punto equivale al coseno)."""
    x = np.asarray(x, dtype=np.float32)
    norm = float(np.linalg.norm(x))
    return x / norm if norm > 1e-10 else x
