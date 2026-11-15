"""Puente motor -> bloque C (vectorial/), que corre como servicio aparte.

El indice IVF vive en el bloque C y no dentro del motor, por dos razones. La
primera es que sus vectores los produce un modelo (SFace sobre YuNet) que
arrastra opencv y onnxruntime, y meter eso en la imagen del motor la vuelve
inmanejable. La segunda es que los 128 flotantes de cada rostro ocuparian mas
que toda la fila que los acompana, asi que duplicarlos en las paginas de datos
no compra nada: la tabla guarda la referencia y el indice guarda el vector.

El motor habla con el por HTTP, igual que el frontend habla con el motor. Si el
servicio no esta arriba, las consultas vectoriales fallan con un mensaje que
dice que levantarlo, y el resto del motor sigue funcionando.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from functools import lru_cache

from .errors import StorageUnavailableError

BASE_POR_DEFECTO = "http://127.0.0.1:8003"
TIEMPO_LIMITE = float(os.getenv("QE_VECTORIAL_TIMEOUT", "30"))
PAGINA_RUTAS = 5000


def base_url() -> str:
    return os.getenv("QE_VECTORIAL_URL", BASE_POR_DEFECTO).rstrip("/")


def _pedir(ruta: str, cuerpo: dict | None = None, metodo: str = "GET") -> dict:
    url = f"{base_url()}{ruta}"
    datos = None
    cabeceras = {"Accept": "application/json"}
    if cuerpo is not None:
        datos = json.dumps(cuerpo).encode("utf-8")
        cabeceras["Content-Type"] = "application/json"
    peticion = urllib.request.Request(url, data=datos, headers=cabeceras, method=metodo)
    try:
        with urllib.request.urlopen(peticion, timeout=TIEMPO_LIMITE) as respuesta:
            return json.load(respuesta)
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "ignore")[:200]
        raise StorageUnavailableError(
            f"el bloque vectorial respondio {error.code} en {ruta}: {detalle}"
        ) from error
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        raise StorageUnavailableError(
            f"no se alcanza el bloque vectorial en {base_url()} ({error}). "
            "Levantalo con 'python vectorial/main.py serve' o con docker compose."
        ) from error


def disponible() -> bool:
    try:
        return bool(_pedir("/health").get("ok"))
    except StorageUnavailableError:
        return False


def estado() -> dict:
    return _pedir("/api/status")


@lru_cache(maxsize=4)
def rutas_indexadas(firma: str) -> tuple[str, ...]:
    """docid -> ruta relativa. La firma invalida la cache cuando cambia el indice."""
    recogidas: list[str] = []
    while True:
        pagina = _pedir(f"/api/paths?offset={len(recogidas)}&limit={PAGINA_RUTAS}")
        nuevas = pagina.get("paths", [])
        if not nuevas:
            break
        recogidas.extend(fila["path"] for fila in nuevas)
        if len(recogidas) >= int(pagina.get("total", 0)):
            break
    return tuple(recogidas)


def incrustar(contenido: bytes, nombre: str, modo: str = "face"):
    """La imagen de consulta convertida en vector por el bloque C.

    Va como multipart porque el detector de rostros necesita los pixeles, no un
    vector: es el unico punto donde el motor manda una imagen y no numeros.
    """
    frontera = "----motorconsultas" + uuid.uuid4().hex
    cuerpo = b"".join([
        f"--{frontera}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{nombre}"\r\n'.encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        contenido,
        f"\r\n--{frontera}\r\n".encode(),
        b'Content-Disposition: form-data; name="mode"\r\n\r\n',
        modo.encode(),
        f"\r\n--{frontera}--\r\n".encode(),
    ])
    peticion = urllib.request.Request(
        f"{base_url()}/api/embed", data=cuerpo, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={frontera}"},
    )
    try:
        with urllib.request.urlopen(peticion, timeout=TIEMPO_LIMITE) as respuesta:
            return json.load(respuesta)["vector"]
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "ignore")[:200]
        raise StorageUnavailableError(
            f"el bloque vectorial no pudo leer la imagen ({error.code}): {detalle}"
        ) from error
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        raise StorageUnavailableError(
            f"no se alcanza el bloque vectorial en {base_url()} ({error})"
        ) from error


def imagen(ruta: str) -> tuple[bytes, str]:
    """Los bytes de un rostro indexado, para que el motor los reenvie."""
    destino = f"{base_url()}/api/image_by_path?path={urllib.parse.quote(ruta)}"
    try:
        with urllib.request.urlopen(destino, timeout=TIEMPO_LIMITE) as respuesta:
            return respuesta.read(), respuesta.headers.get("Content-Type", "image/png")
    except urllib.error.HTTPError as error:
        raise StorageUnavailableError(f"el bloque vectorial no tiene {ruta}") from error
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        raise StorageUnavailableError(
            f"no se alcanza el bloque vectorial en {base_url()} ({error})") from error


def buscar(vector, modo: str, k: int, nprobe: int, max_candidatos: int) -> dict:
    """Filter y Refine dentro del bloque C: devuelve los k mas parecidos."""
    return _pedir("/api/search_vector", {
        "vector": list(vector), "mode": modo, "k": k,
        "nprobe": nprobe, "max_candidates": max_candidatos,
    }, metodo="POST")


def puntuar(vector, modo: str, ids) -> dict:
    """Pre-filtering: el motor ya acoto las filas y solo pide su similitud."""
    return _pedir("/api/score", {
        "vector": list(vector), "mode": modo, "ids": list(ids),
    }, metodo="POST")
