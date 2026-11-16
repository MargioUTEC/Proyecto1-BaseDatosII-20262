# Experimento 9: consultas hibridas, eleccion entre pre y post filtering.
#   A) barrido de selectividad del filtro relacional: estrategia elegida, bloques y tiempo
#   B) los dos costos de referencia: escaneo completo de la tabla y ranking sin filtro
#   C) efecto de nprobe sobre lo que sondea el indice y sobre la estrategia elegida
#   Necesita el bloque C sirviendo en 8003 y el dataset indexado.
#   python benchmarks/experimento9_hibrido.py

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "benchmarks", "results")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend", "query-engine"))

from queryengine import vectorsearch  # noqa: E402
from queryengine.bootstrap import Settings, build_engine  # noqa: E402

TABLA = "rostros"
FILTROS = [
    ("id < 10", 10),
    ("id < 100", 100),
    ("id < 500", 500),
    ("id < 1000", 1_000),
    ("id < 2600", 2_600),
    ("id < 5000", 5_000),
    ("id < 9000", 9_000),
    ("id > 2", 12_997),
]
NPROBES = (1, 2, 4, 8, 16)
K = 5
REPETICIONES = 5
POR_LOTE = 500


def rutas(limite: int) -> list[str]:
    recogidas: list[str] = []
    while True:
        pagina = vectorsearch._pedir(
            f"/api/paths?offset={len(recogidas)}&limit=5000")
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


def montar(caminos: list[str], carpeta: str, nprobe: int):
    engine = build_engine(Settings(backend="disk", catalog_path=None,
                                   data_dir=carpeta, table_dir=carpeta))
    ancho = max(len(r) for r in caminos) + 8
    engine.execute(f"CREATE TABLE {TABLA} (id INT PRIMARY KEY, lote CHAR(8), "
                   f"embedding VARCHAR({ancho})) USING HEAP;")
    for comienzo in range(0, len(caminos), POR_LOTE):
        trozo = caminos[comienzo:comienzo + POR_LOTE]
        valores = ", ".join(
            f"({numero(r)}, '{r.split('/')[0]}', '{r}')" for r in trozo)
        engine.execute(f"INSERT INTO {TABLA} VALUES {valores};")
    engine.execute(f"CREATE INDEX ix_ivf ON {TABLA} (embedding) USING IVF "
                   f"WITH (mode = 'face', nprobe = {nprobe});")
    return engine


def vector_de(ruta: str) -> list[float]:
    contenido, _ = vectorsearch.imagen(ruta)
    return vectorsearch.incrustar(contenido, ruta.rsplit("/", 1)[-1], "face")


def medir(engine, vector, filtro: str | None) -> dict:
    literal = "[" + ", ".join(f"{v:.6f}" for v in vector) + "]"
    donde = f" WHERE {filtro}" if filtro else ""
    sql = (f"SELECT id, lote, embedding <=> '{literal}' AS similitud "
           f"FROM {TABLA}{donde} ORDER BY similitud DESC LIMIT {K};")
    engine.execute(sql)
    muestras = []
    for _ in range(REPETICIONES):
        resultado = engine.execute(sql)
        muestras.append(resultado)
    tiempos = sorted(r.metrics.execution_ms for r in muestras)
    ultima = muestras[-1]
    nodo = _nodo_vectorial(ultima.plan) or {}
    corrida = nodo.get("runtime", {}) or {}
    estrategia = ("pre" if "pre-filtering" in ultima.plan_text
                  else "post" if "post-filtering" in ultima.plan_text else "directo")
    return {
        "filtro": filtro or "(ninguno)",
        "estrategia": estrategia,
        "filas": len(ultima.rows),
        "candidatos": corrida.get("candidates", 0),
        "emitidas": corrida.get("returned", 0),
        "lecturas_indice": corrida.get("index_reads", 0),
        "lecturas_tabla": corrida.get("table_reads", 0),
        "bloques": ultima.metrics.disk_reads,
        "ms": round(statistics.median(tiempos), 3),
    }


def _nodo_vectorial(nodo: dict | None) -> dict | None:
    if not nodo:
        return None
    if nodo.get("node") in ("IVFScan", "IVFRefine"):
        return nodo
    for hijo in nodo.get("children", []):
        hallado = _nodo_vectorial(hijo)
        if hallado is not None:
            return hallado
    return None


def escribir(nombre: str, filas: list[dict]) -> None:
    os.makedirs(RESULTS, exist_ok=True)
    destino = os.path.join(RESULTS, nombre)
    with open(destino, "w", newline="", encoding="utf-8") as salida:
        escritor = csv.DictWriter(salida, fieldnames=list(filas[0]))
        escritor.writeheader()
        escritor.writerows(filas)


def tabla(titulo: str, filas: list[dict]) -> None:
    print(f"\n{titulo}")
    campos = list(filas[0])
    print("  " + "  ".join(f"{c:>14}" for c in campos))
    for fila in filas:
        print("  " + "  ".join(f"{fila[c]!s:>14}" for c in campos))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limite", default=0, type=int)
    parser.add_argument("--nprobe", default=4, type=int)
    opciones = parser.parse_args()

    if not vectorsearch.disponible():
        print(f"el bloque C no responde en {vectorsearch.base_url()}")
        return 1
    estado = vectorsearch.estado()
    print(f"bloque C: {estado['images']} imagenes, {estado['faces']} rostros, "
          f"{estado['face_clusters']} listas, tope {estado['k_max']} resultados")

    caminos = rutas(opciones.limite)
    consulta = vector_de(caminos[42 % len(caminos)])

    with tempfile.TemporaryDirectory() as carpeta:
        engine = montar(caminos, carpeta, opciones.nprobe)
        filas = int(engine.catalog.statistics(TABLA).row_count)
        print(f"tabla: {filas} filas en "
              f"{engine.tables()[0]['page_count']} paginas de 4096 bytes")

        barrido = [medir(engine, consulta, None)]
        for filtro, deja in FILTROS:
            fila = medir(engine, consulta, filtro)
            fila["deja"] = deja
            fila["fraccion_pct"] = round(100 * deja / filas, 2)
            barrido.append(fila)
        barrido[0]["deja"] = filas
        barrido[0]["fraccion_pct"] = 100.0

        completo = engine.execute(f"SELECT id FROM {TABLA} WHERE lote = 'ninguno';")
        referencias = [
            {"ruta": "escaneo completo (SeqScan + Filter)",
             "bloques": completo.metrics.disk_reads,
             "ms": round(completo.metrics.execution_ms, 3)},
            {"ruta": "ranking por el IVF, sin filtro",
             "bloques": barrido[0]["bloques"], "ms": barrido[0]["ms"]},
        ]

    sondeo = []
    for nprobe in NPROBES:
        with tempfile.TemporaryDirectory() as carpeta:
            engine = montar(caminos, carpeta, nprobe)
            sin = medir(engine, consulta, None)
            medio = medir(engine, consulta, "id < 1000")
            sondeo.append({
                "nprobe": nprobe,
                "candidatos": sin["candidatos"],
                "fraccion_pct": round(100 * sin["candidatos"] / filas, 2),
                "ms_sin_filtro": sin["ms"],
                "estrategia_id_lt_1000": medio["estrategia"],
                "bloques_id_lt_1000": medio["bloques"],
                "ms_id_lt_1000": medio["ms"],
            })

    escribir("experimento9_estrategia.csv", barrido)
    escribir("experimento9_referencias.csv", referencias)
    escribir("experimento9_nprobe.csv", sondeo)
    tabla("A) estrategia elegida por selectividad del filtro", barrido)
    tabla("B) costos de referencia", referencias)
    tabla("C) efecto de nprobe", sondeo)
    print(f"\nResultados en {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
