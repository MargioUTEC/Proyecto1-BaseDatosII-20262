# Ingesta de LFW: toma hasta 13 000 imágenes (una carpeta por identidad, en orden alfabético)
# y escribe data/lfw/personas.csv y data/lfw/rostros.csv para COPY.
#   python scripts/ingest_lfw.py --origen <carpeta descargada>
#   python scripts/ingest_lfw.py --descargar      (requiere pip install gdown)

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DRIVE_URL = "https://drive.google.com/drive/folders/1tZUcXDBeOibC6jcMCtgRRz67pzrAHeHL"
LIMITE = 13_000
EXTENSIONES = (".jpg", ".jpeg", ".png")
_SUFIJO = re.compile(r"_\d{4}$")


def descargar(destino: str) -> str:
    # Descarga la carpeta de Drive con gdown (máx. 50 archivos por carpeta en gdown).
    os.makedirs(destino, exist_ok=True)
    try:
        subprocess.run([sys.executable, "-m", "gdown", "--folder", DRIVE_URL, "-O", destino,
                        "--remaining-ok"], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise SystemExit(
            "No se pudo descargar con gdown. Instálalo con 'pip install gdown' o descarga las "
            f"carpetas a mano desde {DRIVE_URL} y usa --origen.\nDetalle: {exc}"
        ) from exc
    return destino


def identidad(ruta: str, origen: str) -> str:
    carpeta = os.path.relpath(os.path.dirname(ruta), origen)
    if carpeta not in (".", ""):
        return os.path.basename(carpeta)
    return _SUFIJO.sub("", os.path.splitext(os.path.basename(ruta))[0])


def recolectar(origen: str) -> list[tuple[str, str]]:
    # (identidad, ruta) de cada imagen, en orden estable.
    encontradas = []
    for carpeta, subcarpetas, archivos in os.walk(origen):
        subcarpetas.sort()
        for archivo in sorted(archivos):
            if archivo.lower().endswith(EXTENSIONES):
                ruta = os.path.join(carpeta, archivo)
                encontradas.append((identidad(ruta, origen), ruta))
    encontradas.sort(key=lambda item: (item[0].lower(), os.path.basename(item[1])))
    return encontradas


def dimensiones(ruta: str) -> tuple[int, int]:
    try:
        from PIL import Image
    except ImportError:
        return 0, 0
    try:
        with Image.open(ruta) as imagen:
            return imagen.size
    except OSError:
        return 0, 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta de LFW (máx. 13 000 imágenes)")
    parser.add_argument("--origen", help="carpeta con las imágenes descargadas")
    parser.add_argument("--descargar", action="store_true", help="bajar la carpeta de Drive con gdown")
    parser.add_argument("--destino", default=os.path.join(DATA, "lfw"))
    parser.add_argument("--limite", type=int, default=LIMITE)
    parser.add_argument("--sin-copiar", action="store_true",
                        help="no copiar las imágenes; el origen debe estar dentro de data/")
    args = parser.parse_args()

    if args.descargar:
        origen = descargar(os.path.join(args.destino, "descarga"))
    elif args.origen:
        origen = os.path.abspath(os.path.expanduser(args.origen))
    else:
        parser.error("indica --origen <carpeta> o --descargar")
    if not os.path.isdir(origen):
        raise SystemExit(f"no existe la carpeta {origen}")

    todas = recolectar(origen)
    elegidas = todas[: args.limite]
    if not elegidas:
        raise SystemExit(f"no se encontraron imágenes {EXTENSIONES} en {origen}")
    os.makedirs(args.destino, exist_ok=True)
    destino_imgs = os.path.join(args.destino, "imagenes")

    personas: dict[str, int] = {}
    conteo: dict[int, int] = {}
    filas = []
    for numero, (nombre, ruta) in enumerate(elegidas, start=1):
        persona_id = personas.setdefault(nombre, len(personas) + 1)
        conteo[persona_id] = conteo.get(persona_id, 0) + 1
        if args.sin_copiar:
            final = ruta
        else:
            final = os.path.join(destino_imgs, nombre, os.path.basename(ruta))
            if not os.path.exists(final):
                os.makedirs(os.path.dirname(final), exist_ok=True)
                shutil.copy2(ruta, final)
        relativa = os.path.relpath(final, DATA).replace(os.sep, "/")
        if relativa.startswith(".."):
            raise SystemExit(f"{final} queda fuera de data/: quita --sin-copiar")
        ancho, alto = dimensiones(final)
        filas.append((numero, persona_id, nombre.replace("_", " "), relativa, ancho, alto,
                      os.path.getsize(final)))
        if numero % 1000 == 0:
            print(f"  {numero} imágenes procesadas")

    with open(os.path.join(args.destino, "personas.csv"), "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["persona_id", "nombre", "imagenes"])
        for nombre, persona_id in personas.items():
            writer.writerow([persona_id, nombre.replace("_", " "), conteo[persona_id]])
    with open(os.path.join(args.destino, "rostros.csv"), "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "persona_id", "persona", "archivo", "ancho", "alto", "bytes"])
        writer.writerows(filas)

    varias = sum(1 for n in conteo.values() if n > 1)
    print(f"{len(filas)} imágenes de {len(personas)} identidades "
          f"({varias} con más de una foto) de {len(todas)} disponibles")
    print(f"CSV en {args.destino}: personas.csv, rostros.csv")


if __name__ == "__main__":
    main()
