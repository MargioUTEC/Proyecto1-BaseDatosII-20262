"""La aplicacion de identificacion facial, montada sobre el motor.

El recorrido completo de una consulta, que es lo que el entregable pide
demostrar: la foto entra, el bloque C la convierte en un vector de 128
dimensiones, el motor arma una sentencia SQL con ese vector como literal y el
planificador decide por donde resolverla. Lo que vuelve no es solo la lista de
caras parecidas, es tambien el plan que se ejecuto y lo que costo.

El endpoint no esquiva el motor: la busqueda viaja como SQL de verdad, con su
operador <=> y su ORDER BY, para que la demostracion sea del gestor y no de un
atajo escrito aparte.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import vectorsearch
from ..errors import QueryEngineError

MAXIMO_BYTES = 12 * 1024 * 1024
ARCHIVO = File(...)
CAMPO_TABLA = Form("rostros")
CAMPO_COLUMNA = Form("embedding")
CAMPO_K = Form(5)
CAMPO_MODO = Form("face")
CAMPO_FILTRO = Form("")
CAMPO_PROYECCION = Form("")
TABLA_POR_DEFECTO = "rostros"
COLUMNA_POR_DEFECTO = "embedding"


def crear_router(engine) -> APIRouter:
    router = APIRouter()

    @router.get("/api/identificacion/estado")
    def estado() -> dict:
        """Si el bloque vectorial responde y que tiene indexado."""
        try:
            return {"disponible": True, "servicio": vectorsearch.base_url(),
                    **vectorsearch.estado()}
        except QueryEngineError as error:
            return {"disponible": False, "servicio": vectorsearch.base_url(),
                    "detalle": str(error)}

    @router.post("/api/identificacion/buscar")
    async def buscar(
        file: UploadFile = ARCHIVO,
        tabla: str = CAMPO_TABLA,
        columna: str = CAMPO_COLUMNA,
        k: int = CAMPO_K,
        modo: str = CAMPO_MODO,
        filtro: str = CAMPO_FILTRO,
        proyeccion: str = CAMPO_PROYECCION,
    ) -> dict:
        contenido = await file.read(MAXIMO_BYTES + 1)
        if len(contenido) > MAXIMO_BYTES:
            raise HTTPException(400, f"la imagen supera {MAXIMO_BYTES // (1024 * 1024)} MB")
        if not contenido:
            raise HTTPException(400, "no llego ninguna imagen")

        comenzo = time.perf_counter()
        try:
            vector = vectorsearch.incrustar(contenido, file.filename or "consulta.png", modo)
        except QueryEngineError as error:
            raise HTTPException(503, str(error)) from error
        ms_vector = (time.perf_counter() - comenzo) * 1000

        sentencia = _sentencia(engine, tabla, columna, vector, k, filtro, proyeccion)
        resultado = engine.execute(sentencia)
        datos = resultado.to_dict()
        datos["sql"] = sentencia
        datos["dim"] = len(vector)
        datos["embedding_ms"] = round(ms_vector, 2)
        datos["estrategia"] = _estrategia(datos.get("plan"))
        return datos

    return router


def _sentencia(engine, tabla: str, columna: str, vector, k: int, filtro: str,
               proyeccion: str) -> str:
    """El SQL que resuelve la consulta, con el vector como literal."""
    _validar_identificador(tabla, "tabla")
    _validar_identificador(columna, "columna")
    columnas = _columnas(engine, tabla, proyeccion)
    literal = "[" + ", ".join(f"{float(valor):.6f}" for valor in vector) + "]"
    partes = [f"SELECT {columnas}, {columna} <=> '{literal}' AS similitud FROM {tabla}"]
    if filtro.strip():
        if ";" in filtro:
            raise HTTPException(400, "el filtro no puede contener ';'")
        partes.append(f"WHERE {filtro.strip()}")
    partes.append(f"ORDER BY similitud DESC LIMIT {max(1, min(int(k), 50))}")
    return " ".join(partes) + ";"


def _columnas(engine, tabla: str, proyeccion: str) -> str:
    """Las columnas van nombradas, porque '*' no convive con una expresion."""
    if not proyeccion.strip():
        try:
            return ", ".join(engine.catalog.table(tabla).column_names)
        except QueryEngineError as error:
            raise HTTPException(400, str(error)) from error
    nombres = [parte.strip() for parte in proyeccion.split(",") if parte.strip()]
    for nombre in nombres:
        _validar_identificador(nombre, "columna")
    return ", ".join(nombres)


def _validar_identificador(nombre: str, que: str) -> None:
    limpio = nombre.strip()
    if not limpio or not limpio.replace("_", "").isalnum():
        raise HTTPException(400, f"nombre de {que} invalido: {nombre!r}")


def _estrategia(plan: dict | None) -> str:
    """La estrategia que el planificador dejo escrita en el nodo vectorial."""
    if not plan:
        return ""
    if plan.get("strategy"):
        return str(plan["strategy"])
    for hijo in plan.get("children", []):
        encontrada = _estrategia(hijo)
        if encontrada:
            return encontrada
    return ""
