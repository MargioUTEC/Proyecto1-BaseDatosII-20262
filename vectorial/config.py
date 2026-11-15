"""Bloque de configuración del Bloque C (rutas, modelos y parámetros por defecto).

Todo lo que quieras ajustar sin tocar la lógica está aquí. Las rutas se pueden
cambiar con variables de entorno (útil en Docker).
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Bloque de rutas
# ---------------------------------------------------------------------------
BASE = Path(__file__).resolve().parent
RAIZ_REPO = BASE.parent
DIR_INDICE = Path(os.environ.get("VECTORIAL_INDEX", RAIZ_REPO / "data" / "indices_c"))
DIR_RESULTADOS = Path(os.environ.get("VECTORIAL_RESULTS", RAIZ_REPO / "benchmarks" / "results"))
DIR_WEB = BASE / "web"
# Si el dataset se movió desde que se indexó (p. ej. dentro de Docker).
_data_root = os.environ.get("VECTORIAL_DATA_ROOT")
DATA_ROOT_OVERRIDE = Path(_data_root) if _data_root else None

# ---------------------------------------------------------------------------
# Bloque de servidor
# ---------------------------------------------------------------------------
HOST = "127.0.0.1"
PUERTO = 8003
MAX_BYTES_CONSULTA = 12 * 1024 * 1024  # tamaño máximo de la imagen de consulta
K_MAXIMO = 50

# ---------------------------------------------------------------------------
# Bloque de modelos faciales (OpenCV Zoo)
# ---------------------------------------------------------------------------
# Si la descarga falla por "SHA256 inválido", pon VECTORIAL_VERIFICAR_SHA=0
# (o corrige el hash de abajo) y vuelve a intentar.
VERIFICAR_SHA256 = os.environ.get("VECTORIAL_VERIFICAR_SHA", "1") != "0"
MODEL_INFO = {
    "yunet": (
        "face_detection_yunet_2023mar.onnx",
        "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "sface": (
        "face_recognition_sface_2021dec.onnx",
        "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
}
UMBRAL_DETECCION = 0.7
TIMEOUT_DESCARGA_S = 90

# ---------------------------------------------------------------------------
# Bloque de imágenes
# ---------------------------------------------------------------------------
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
DIM_EMBEDDING = 128  # SFace

# ---------------------------------------------------------------------------
# Bloque de parámetros por defecto de construcción y búsqueda
# ---------------------------------------------------------------------------
LIMITE_BUILD = 1000
PALABRAS_VISUALES = 64
CLUSTERS_IVF = 32
IMAGENES_ENTRENAMIENTO = 300
DESCRIPTORES_POR_IMAGEN = 32
LADO_MAX = 640
NFEATURES_SIFT = 400
SEMILLA = 42
CHECKPOINT_CADA_S = 30 * 60  # el build guarda su avance cada 30 min (cámbialo aquí)

NPROBE = 4
PALABRAS_CONSULTA = 8
MAX_CANDIDATOS = 500
K_DEFECTO = 5

# ---------------------------------------------------------------------------
# Bloque de experimentos (7 = IVF rostros, 8 = BoVW)
# ---------------------------------------------------------------------------
EXP7_NPROBE = [1, 2, 4, 8]
EXP8_PALABRAS = [2, 4, 8, 16]
CONSULTAS_BENCHMARK = 20
ARCHIVO_EXP7 = "experimento7_ivf_rostros.csv"
ARCHIVO_EXP8 = "experimento8_bovw.csv"
