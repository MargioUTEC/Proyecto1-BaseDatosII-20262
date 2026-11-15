"""Bloque de modelos faciales: descarga verificada de YuNet/SFace y extracción de embeddings.

Los modelos son de OpenCV Zoo; aquí no se entrena ninguna red neuronal.
"""
from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from config import MODEL_INFO, TIMEOUT_DESCARGA_S, UMBRAL_DETECCION, VERIFICAR_SHA256
from imagenes import unit


# ---------------------------------------------------------------------------
# Bloque para descargar los modelos (una sola vez)
# ---------------------------------------------------------------------------
def ensure_models(folder: Path) -> tuple[Path, Path]:
    """Devuelve (ruta YuNet, ruta SFace); los descarga si no existen."""
    folder.mkdir(parents=True, exist_ok=True)
    outputs = []
    for name, url, digest in MODEL_INFO.values():
        path = folder / name
        if path.exists():
            if VERIFICAR_SHA256 and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"Modelo {path} tiene SHA256 incorrecto; elimina el archivo y vuelve a intentar "
                                   "(o usa VECTORIAL_VERIFICAR_SHA=0).")
            outputs.append(path)
            continue
        print(f"Descargando modelo OpenCV {name} (una sola vez)...")
        tmp = path.with_suffix(".part")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            sha = hashlib.sha256()
            with urllib.request.urlopen(request, timeout=TIMEOUT_DESCARGA_S) as resp, tmp.open("wb") as fp:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    fp.write(chunk)
                    sha.update(chunk)
            if VERIFICAR_SHA256 and sha.hexdigest() != digest:
                raise RuntimeError(f"SHA256 inválido de {name} (obtenido {sha.hexdigest()}): "
                                   "descarga incompleta o hash desactualizado.")
            tmp.replace(path)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"No se pudo descargar {name}. Descárgalo manualmente de:\n{url}\n"
                f"y colócalo en {folder}. Detalle: {e}"
            ) from e
        outputs.append(path)
    return outputs[0], outputs[1]


# ---------------------------------------------------------------------------
# Bloque del extractor de embeddings faciales
# ---------------------------------------------------------------------------
class FaceEmbedder:
    """Detector YuNet + embedding SFace de 128 dimensiones, sin reconocer identidades."""

    def __init__(self, model_dir: Path):
        det, rec = ensure_models(model_dir)
        if not hasattr(cv2, "FaceDetectorYN") or not hasattr(cv2, "FaceRecognizerSF"):
            raise RuntimeError("Se requiere opencv-contrib-python moderno. Reinstala requirements.txt en un venv limpio.")
        self.detector = cv2.FaceDetectorYN.create(str(det), "", (320, 320), score_threshold=UMBRAL_DETECCION)
        self.recognizer = cv2.FaceRecognizerSF.create(str(rec), "")

    def extract(self, img: np.ndarray) -> np.ndarray | None:
        """Embedding unitario del rostro más grande, o None si no hay rostro."""
        h, w = img.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(img)
        if faces is None or len(faces) == 0:
            return None
        face = max(faces, key=lambda f: float(f[2] * f[3]))
        aligned = self.recognizer.alignCrop(img, face)
        return unit(self.recognizer.feature(aligned).reshape(-1))
