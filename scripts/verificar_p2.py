"""Comprueba que los cuatro bloques del entregable 2 responden en un solo motor.

Levanta las tablas de prueba de cada bloque, corre una consulta de cada familia
y revisa que el plan elegido sea el que corresponde: que la espacial use el
R-Tree, la textual el indice invertido, y que la vectorial decida entre pre y
post filtering segun lo selectivo que sea el filtro relacional.

    python scripts/verificar_p2.py
    python scripts/verificar_p2.py --motor http://127.0.0.1:8001 --tabla rostros
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

MOTOR = "http://127.0.0.1:8001"
VECTORIAL = "http://127.0.0.1:8003"
RAIZ = Path(__file__).resolve().parent.parent

# El radio y DISTANCE trabajan en grados sobre el plano de coordenadas, que es
# como el R-Tree del bloque A mide: 0.03 son unos 3.3 km en Lima.
LOCALES = [
    (1, "Sede Barranco", -77.0220, -12.1490),
    (2, "Sede Miraflores", -77.0282, -12.1210),
    (3, "Sede San Isidro", -77.0365, -12.0970),
    (4, "Sede Surco", -76.9900, -12.1450),
    (5, "Sede Callao", -77.1180, -12.0570),
]
EXPEDIENTES = [
    (1, "camara con falla de iluminacion en el ingreso principal"),
    (2, "verificacion de rostro sin mascarilla en el torniquete"),
    (3, "credencial vencida reportada en la sede de miraflores"),
    (4, "falla de iluminacion intermitente en el estacionamiento"),
    (5, "rostro no reconocido por la camara del comedor"),
]


class Fallo(Exception):
    pass


def pedir(url: str, cuerpo: dict | None = None) -> dict:
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    cabeceras = {"Content-Type": "application/json"} if datos else {}
    peticion = urllib.request.Request(url, data=datos, headers=cabeceras)
    try:
        with urllib.request.urlopen(peticion, timeout=180) as respuesta:
            return json.load(respuesta)
    except urllib.error.HTTPError as error:
        raise Fallo(f"{url} respondio {error.code}: "
                    f"{error.read().decode('utf-8', 'ignore')[:300]}") from error
    except urllib.error.URLError as error:
        raise Fallo(f"no se alcanza {url}: {error.reason}") from error


def subir(url: str, archivo: Path, campos: dict) -> dict:
    borde = uuid.uuid4().hex
    partes = []
    for nombre, valor in campos.items():
        partes.append(f"--{borde}\r\nContent-Disposition: form-data; name=\"{nombre}\"\r\n\r\n"
                      f"{valor}\r\n".encode())
    tipo = mimetypes.guess_type(archivo.name)[0] or "application/octet-stream"
    partes.append(f"--{borde}\r\nContent-Disposition: form-data; name=\"file\"; "
                  f"filename=\"{archivo.name}\"\r\nContent-Type: {tipo}\r\n\r\n".encode())
    partes.append(archivo.read_bytes())
    partes.append(f"\r\n--{borde}--\r\n".encode())
    cuerpo = b"".join(partes)
    peticion = urllib.request.Request(
        url, data=cuerpo,
        headers={"Content-Type": f"multipart/form-data; boundary={borde}"})
    try:
        with urllib.request.urlopen(peticion, timeout=180) as respuesta:
            return json.load(respuesta)
    except urllib.error.HTTPError as error:
        raise Fallo(f"{url} respondio {error.code}: "
                    f"{error.read().decode('utf-8', 'ignore')[:300]}") from error


class Informe:
    def __init__(self):
        self.fallos = 0

    def revisar(self, nombre: str, condicion: bool, detalle: str = "") -> None:
        marca = "ok  " if condicion else "FALLO"
        print(f"  {marca} {nombre:<46} {detalle}")
        if not condicion:
            self.fallos += 1

    def nota(self, texto: str) -> None:
        print(f"       {texto}")


def sql(motor: str, sentencia: str) -> dict:
    return pedir(f"{motor}/api/query", {"sql": sentencia})


def rehacer_tabla(motor: str, nombre: str, creacion: str, inserciones: str) -> None:
    existentes = {t["name"].lower() for t in pedir(f"{motor}/api/tables").get("tables", [])}
    if nombre.lower() in existentes:
        sql(motor, f"DROP TABLE {nombre};")
    sql(motor, creacion)
    sql(motor, inserciones)


def bloque_a(motor: str, informe: Informe) -> None:
    print("\nBloque A  espacial (R-Tree paginado)")
    rehacer_tabla(
        motor, "locales",
        "CREATE TABLE locales (id INT PRIMARY KEY, nombre CHAR(24), "
        "ubicacion CHAR(40)) USING HEAP;",
        "INSERT INTO locales VALUES " + ", ".join(
            f"({i}, '{n}', 'POINT({lon} {lat})')" for i, n, lon, lat in LOCALES) + ";")
    sql(motor, "CREATE INDEX ix_locales_rtree ON locales (ubicacion) USING RTREE;")

    radio = sql(motor, "SELECT id, nombre FROM locales "
                       "WHERE ubicacion WITHIN_RADIUS((-77.0282, -12.1210), 0.03);")
    dentro = sorted(fila[0] for fila in radio["rows"])
    informe.revisar("busqueda por radio usa el R-Tree",
                    "RTreeRadiusScan" in radio["plan_text"],
                    f"{len(dentro)} locales")
    informe.revisar("el radio deja fuera lo que no alcanza",
                    dentro == [1, 2, 3], f"ids {dentro}")

    knn = sql(motor, "SELECT id, nombre FROM locales "
                     "ORDER BY DISTANCE(ubicacion, (-77.0282, -12.1210)) LIMIT 3;")
    informe.revisar("k-NN usa el R-Tree", "RTreeKNNScan" in knn["plan_text"],
                    " < ".join(str(f[0]) for f in knn["rows"]))
    informe.revisar("el vecino mas cercano es el propio punto",
                    bool(knn["rows"]) and knn["rows"][0][0] == 2)


def bloque_b(motor: str, informe: Informe) -> None:
    print("\nBloque B  textual (indice invertido SPIMI)")
    rehacer_tabla(
        motor, "expedientes",
        "CREATE TABLE expedientes (id INT PRIMARY KEY, cuerpo VARCHAR(200)) USING HEAP;",
        "INSERT INTO expedientes VALUES " + ", ".join(
            f"({i}, '{texto}')" for i, texto in EXPEDIENTES) + ";")
    sql(motor, "CREATE INDEX ix_exp_inv ON expedientes (cuerpo) USING INVERTED;")

    ranking = sql(motor, "SELECT id, COSINE_SCORE() AS relevancia FROM expedientes "
                         "WHERE cuerpo MATCH 'falla iluminacion' "
                         "ORDER BY relevancia DESC LIMIT 5;")
    informe.revisar("ranking por coseno usa el indice invertido",
                    "InvertedIndexScan" in ranking["plan_text"],
                    f"{len(ranking['rows'])} documentos")
    informe.revisar("el mejor documento trae relevancia positiva",
                    bool(ranking["rows"]) and ranking["rows"][0][1] > 0,
                    f"{ranking['rows'][0][1]:.4f}" if ranking["rows"] else "")

    booleana = sql(motor, "SELECT id FROM expedientes "
                          "WHERE cuerpo MATCH 'rostro AND NOT mascarilla';")
    informe.revisar("consulta booleana con NOT",
                    [f[0] for f in booleana["rows"]] == [5],
                    f"ids {[f[0] for f in booleana['rows']]}")


def bloque_c(motor: str, vectorial: str, tabla: str, informe: Informe) -> dict:
    print("\nBloque C  vectorial (IVF sobre rostros) y planificador hibrido")
    estado = pedir(f"{motor}/api/identificacion/estado")
    informe.revisar("el motor ve el servicio vectorial", bool(estado.get("disponible")),
                    f"{estado.get('images', 0)} imagenes, {estado.get('faces', 0)} rostros")
    if not estado.get("disponible"):
        raise Fallo(f"el bloque C no responde: {estado.get('detalle', '')}")

    cargadas = filas_de(motor, tabla)
    informe.revisar(f"la tabla {tabla} esta cargada", cargadas > 0, f"{cargadas} filas")

    muestra = pedir(f"{vectorial}/api/paths?offset=0&limit=1")["paths"][0]
    ruta_absoluta = RAIZ / "data" / "ffhq" / muestra["path"]

    consulta = subir(f"{motor}/api/identificacion/buscar", ruta_absoluta,
                     {"k": "5", "proyeccion": "id,lote,embedding", "filtro": ""})
    informe.revisar("sin filtro el IVF rankea directo",
                    "IVFScan" in consulta["plan_text"] and "ranking directo" in consulta["plan_text"],
                    consulta.get("estrategia", ""))
    primera = consulta["rows"][0] if consulta["rows"] else []
    similitud = primera[consulta["columns"].index("similitud")] if primera else 0
    informe.revisar("la foto de consulta se encuentra a si misma",
                    bool(primera) and similitud > 0.99,
                    f"{muestra['path']} -> {similitud * 100:.1f}%")
    runtime = nodo_vectorial(consulta["plan"]) or {}
    telemetria = runtime.get("runtime", {})
    informe.nota(f"candidatos {telemetria.get('candidates')} de {cargadas} filas, "
                 f"embedding {consulta['embedding_ms']:.0f} ms, "
                 f"ejecucion {consulta['metrics']['execution_ms']:.2f} ms")

    selectivo = subir(f"{motor}/api/identificacion/buscar", ruta_absoluta,
                      {"k": "5", "proyeccion": "id,lote,embedding", "filtro": "id < 50"})
    informe.revisar("un filtro selectivo elige pre-filtering",
                    "pre-filtering" in selectivo["plan_text"]
                    and "IVFRefine" in selectivo["plan_text"],
                    selectivo.get("estrategia", ""))

    debil = subir(f"{motor}/api/identificacion/buscar", ruta_absoluta,
                  {"k": "5", "proyeccion": "id,lote,embedding", "filtro": "id > 10"})
    informe.revisar("un filtro debil elige post-filtering",
                    "post-filtering" in debil["plan_text"]
                    and "IVFScan" in debil["plan_text"],
                    debil.get("estrategia", ""))
    return consulta


def bloque_d(motor: str, tabla: str, consulta: dict, informe: Informe) -> None:
    print("\nBloque D  catalogo y vertebra del motor")
    tablas = pedir(f"{motor}/api/tables")["tables"]
    por_nombre = {t["name"].lower(): t for t in tablas}
    familias = {ix["kind"] for t in tablas for ix in t.get("indexes", [])}
    informe.revisar("las tres familias de indice conviven en un catalogo",
                    {"RTREE", "INVERTED", "IVF"} <= familias,
                    " ".join(sorted(familias)))
    informe.revisar(f"{tabla} declara su indice IVF",
                    any(ix["kind"] == "IVF"
                        for ix in por_nombre.get(tabla, {}).get("indexes", [])))
    informe.revisar("los indices corren sobre su estructura real, no el sustituto",
                    all(ix["backend"] != "sustituto en memoria"
                        for t in tablas for ix in t.get("indexes", [])),
                    ", ".join(sorted({ix["backend"] for t in tablas
                                      for ix in t.get("indexes", [])})))

    escaneo = sql(motor, f"SELECT id FROM {tabla} WHERE lote = 'ninguno';")
    bloques_escaneo = escaneo["metrics"]["disk_reads"]
    bloques_indice = consulta["metrics"]["disk_reads"]
    informe.revisar("la via vectorial lee menos bloques que el escaneo completo",
                    bloques_indice < bloques_escaneo,
                    f"{bloques_indice} contra {bloques_escaneo} bloques")
    paginas = por_nombre.get(tabla, {}).get("page_count", 0)
    informe.nota(f"{tabla} ocupa {paginas} paginas de "
                 f"{por_nombre.get(tabla, {}).get('page_size', 0)} bytes")


def filas_de(motor: str, tabla: str) -> int:
    for t in pedir(f"{motor}/api/tables")["tables"]:
        if t["name"].lower() == tabla.lower():
            return int(t["row_count"])
    return 0


def nodo_vectorial(nodo: dict | None) -> dict | None:
    if not nodo:
        return None
    if nodo.get("node") in ("IVFScan", "IVFRefine"):
        return nodo
    for hijo in nodo.get("children", []):
        hallado = nodo_vectorial(hijo)
        if hallado:
            return hallado
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--motor", default=MOTOR)
    parser.add_argument("--vectorial", default=VECTORIAL)
    parser.add_argument("--tabla", default="rostros")
    opciones = parser.parse_args()

    informe = Informe()
    print(f"motor {opciones.motor}  ·  vectorial {opciones.vectorial}")
    try:
        bloque_a(opciones.motor, informe)
        bloque_b(opciones.motor, informe)
        consulta = bloque_c(opciones.motor, opciones.vectorial, opciones.tabla, informe)
        bloque_d(opciones.motor, opciones.tabla, consulta, informe)
    except Fallo as fallo:
        print(f"\n  FALLO {fallo}")
        return 1

    print()
    if informe.fallos:
        print(f"{informe.fallos} comprobaciones fallaron")
        return 1
    print("todo responde")
    return 0


if __name__ == "__main__":
    sys.exit(main())
