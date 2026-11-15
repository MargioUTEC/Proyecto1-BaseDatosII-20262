"""Crea la tabla de rostros en el motor y la enlaza con el indice del bloque C.

La tabla no guarda los vectores. Guarda la ruta de cada imagen, que es la
referencia con la que el indice IVF resuelve su vector, y un par de atributos
relacionales sobre los que filtrar. Eso es lo que convierte una busqueda
vectorial en una consulta hibrida de verdad.

    python scripts/cargar_rostros.py
    python scripts/cargar_rostros.py --motor http://127.0.0.1:8001 --limite 2000
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

MOTOR = "http://127.0.0.1:8001"
VECTORIAL = "http://127.0.0.1:8003"
TABLA = "rostros"
POR_LOTE = 200


def pedir(url: str, cuerpo: dict | None = None) -> dict:
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    cabeceras = {"Content-Type": "application/json"} if datos else {}
    peticion = urllib.request.Request(url, data=datos, headers=cabeceras)
    try:
        with urllib.request.urlopen(peticion, timeout=120) as respuesta:
            return json.load(respuesta)
    except urllib.error.HTTPError as error:
        detalle = error.read().decode("utf-8", "ignore")[:300]
        raise SystemExit(f"{url} respondio {error.code}: {detalle}") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"no se alcanza {url}: {error}") from error


def sql(motor: str, sentencia: str) -> dict:
    return pedir(f"{motor}/api/query", {"sql": sentencia})


def rutas(vectorial: str, limite: int) -> list[str]:
    recogidas: list[str] = []
    while True:
        pagina = pedir(f"{vectorial}/api/paths?offset={len(recogidas)}&limit=5000")
        nuevas = [fila["path"] for fila in pagina.get("paths", [])]
        if not nuevas:
            break
        recogidas.extend(nuevas)
        if len(recogidas) >= int(pagina.get("total", 0)):
            break
        if limite and len(recogidas) >= limite:
            break
    return recogidas[:limite] if limite else recogidas


def numero(ruta: str) -> int:
    nombre = ruta.replace("\\", "/").rsplit("/", 1)[-1].split(".")[0]
    return int(nombre) if nombre.isdigit() else abs(hash(nombre)) % 10_000_000


def lote(ruta: str) -> str:
    partes = ruta.replace("\\", "/").split("/")
    return partes[0] if len(partes) > 1 else "00000"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--motor", default=MOTOR)
    parser.add_argument("--vectorial", default=VECTORIAL)
    parser.add_argument("--tabla", default=TABLA)
    parser.add_argument("--limite", default=0, type=int)
    opciones = parser.parse_args()

    estado = pedir(f"{opciones.vectorial}/api/status")
    print(f"bloque vectorial: {estado['images']} imagenes, {estado['faces']} rostros")

    caminos = rutas(opciones.vectorial, opciones.limite)
    if not caminos:
        raise SystemExit("el bloque vectorial no tiene imagenes indexadas")
    print(f"se cargaran {len(caminos)} rostros en '{opciones.tabla}'")

    ancho = max(len(ruta) for ruta in caminos) + 8
    if _existe(opciones):
        sql(opciones.motor, f"DROP TABLE {opciones.tabla};")
    sql(opciones.motor,
        f"CREATE TABLE {opciones.tabla} ("
        f"  id INT PRIMARY KEY,"
        f"  lote CHAR(8),"
        f"  embedding VARCHAR({ancho})"
        f") USING HEAP;")

    insertadas = 0
    for comienzo in range(0, len(caminos), POR_LOTE):
        trozo = caminos[comienzo:comienzo + POR_LOTE]
        valores = ", ".join(
            f"({numero(ruta)}, '{lote(ruta)}', '{ruta}')" for ruta in trozo)
        sql(opciones.motor, f"INSERT INTO {opciones.tabla} VALUES {valores};")
        insertadas += len(trozo)
        print(f"  {insertadas}/{len(caminos)}", flush=True)

    sql(opciones.motor,
        f"CREATE INDEX ix_{opciones.tabla}_ivf ON {opciones.tabla} (embedding) "
        f"USING IVF WITH (mode = 'face', nprobe = 4);")
    print(f"indice IVF creado sobre {opciones.tabla}(embedding)")

    comprobacion = sql(opciones.motor, f"SELECT * FROM {opciones.tabla} LIMIT 3;")
    print("muestra:", comprobacion["rows"])
    return 0


def _existe(opciones) -> bool:
    tablas = pedir(f"{opciones.motor}/api/tables").get("tables", [])
    return any(tabla["name"].lower() == opciones.tabla.lower() for tabla in tablas)


if __name__ == "__main__":
    sys.exit(main())
