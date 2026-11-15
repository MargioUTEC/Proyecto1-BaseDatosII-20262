"""Descarga las carpetas de FFHQ que usa el proyecto, de forma reanudable.

El dataset vive en una carpeta compartida de Drive con una subcarpeta por cada
mil imagenes. El proyecto usa desde 00000 hasta 13000, que son 14 000 rostros.

Se puede cortar y volver a lanzar: las imagenes que ya estan en disco no se
vuelven a pedir.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from pathlib import Path

import gdown

CARPETA_RAIZ = "1tZUcXDBeOibC6jcMCtgRRz67pzrAHeHL"
ULTIMA = 13000
POR_CARPETA = 1000

IDS = {
    0: "1taHKxS66YKJNhdhiGcEdM6nnE5W9zBb1",
    1000: "1QRt2us_EueWFilF-uJiwAbS4Jsb6IxAW",
    2000: "1QXnHIoc_dmgzSdZQmQJ1I3QNOrRd7CSD",
    3000: "1QHlEZpTtf-Bm9wH0pupFLwYU4R2fJi6e",
    4000: "1tRvhNIMrkl6ypjKJN8Nh0Gr7gqZtPpc4",
    5000: "1sHQK8mx8laO50_b828vQGtmfiQTqZ6Cm",
    6000: "1s-c_sfM1Q1Or82gSW_INaT1v6dpAMY4J",
    7000: "1sh5WdD5oR_1l1CcJ2OH2lqXzdH6G4T2K",
    8000: "1rzpTdOU1xM_sySqHUMcnF6T5LLbaePxs",
    9000: "1tOX9ovjkMmP0ayAtcLndJdKJtKbQ5CdO",
    10000: "1sNpNx25nDQim0morw7LDPFIqXq5HYKyr",
    11000: "1rxtwpfjNE3Ux7c_eC0ZT6G0ECLagGgFg",
    12000: "1ta_GqlOa2TAzF1PCA-2RI4m9-Ux23MIT",
    13000: "1t8Ii1qm63A15L0Wy7mrkYpEThMxD_abp",
}


def destino(base: Path, inicio: int) -> Path:
    return base / f"{inicio:05d}"


def completa(carpeta: Path) -> bool:
    if not carpeta.is_dir():
        return False
    return len(list(carpeta.glob("*.png"))) >= POR_CARPETA


def descargar(base: Path, desde: int, hasta: int, reintentos: int, hilos: int) -> int:
    """Baja carpeta por carpeta.

    Los archivos sueltos de esta unidad compartida no tienen enlace publico, asi
    que la unica via es pedir la carpeta entera; gdown salta las que ya estan en
    disco, de modo que cortar y relanzar no pierde nada.
    """
    base.mkdir(parents=True, exist_ok=True)
    faltantes = 0
    for inicio in range(desde, hasta + 1, POR_CARPETA):
        carpeta = destino(base, inicio)
        if completa(carpeta):
            print(f"[saltada] {carpeta.name}", flush=True)
            continue
        carpeta.mkdir(parents=True, exist_ok=True)
        print(f"[bajando] {carpeta.name}", flush=True)
        comenzo = time.time()
        for intento in range(1, reintentos + 1):
            try:
                gdown.download_folder(id=IDS[inicio], output=str(carpeta), quiet=True,
                                      use_cookies=True, resume=True)
                break
            except Exception as error:
                print(f"[intento {intento}/{reintentos}] {carpeta.name}: "
                      f"{str(error)[:120]}", flush=True)
                time.sleep(min(60, 10 * intento))
        presentes = len(list(carpeta.glob("*.png")))
        print(f"[listo] {carpeta.name}: {presentes} imagenes en "
              f"{time.time() - comenzo:.0f}s", flush=True)
        faltantes += max(0, POR_CARPETA - presentes)
    return faltantes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destino", default="data/ffhq", type=Path)
    parser.add_argument("--desde", default=0, type=int)
    parser.add_argument("--hasta", default=ULTIMA, type=int)
    parser.add_argument("--reintentos", default=3, type=int)
    parser.add_argument("--hilos", default=16, type=int)
    opciones = parser.parse_args()

    faltantes = descargar(opciones.destino, opciones.desde, opciones.hasta,
                          opciones.reintentos, opciones.hilos)
    total = sum(1 for _ in opciones.destino.rglob("*.png"))
    print(f"total en disco: {total} imagenes, faltan {faltantes}", flush=True)
    return 0 if faltantes == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
