# Query Engine — Parser SQL, Planificador y Ejecutor

Microservicio del bloque **BLK 05** del proyecto CS2042. Recibe SQL, decide por
qué ruta de acceso resolverlo, lo ejecuta contra la capa de almacenamiento y
devuelve las tuplas junto al costo exacto en bloques de disco y milisegundos.

No lee ni escribe un solo archivo: toda la E/S física ocurre detrás de los
puertos `StorageEngine` e `IndexManager`, que implementan los módulos de
almacenamiento e índices.

---

## Arranque rápido

```bash
cd backend/query-engine
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

pytest                                              # 175 pruebas
uvicorn queryengine.api.app:app --reload --port 8001
```

Con Docker, desde la raíz del repositorio (levanta también el servicio de
almacenamiento físico):

```bash
docker compose up --build        # storage en :8000, motor en :8001
```

Cliente de línea de comandos:

```bash
python -m queryengine                               # shell interactivo
python -m queryengine -c "SELECT * FROM t LIMIT 5;" # una sentencia
python -m queryengine -f script.sql --plan          # un archivo, mostrando planes
```

Contra bloques reales en disco:

```bash
QE_STORAGE_BACKEND=disk QE_BLK01_PATH=../../storage python -m queryengine
```

---

## Qué SQL entiende

```sql
CREATE TABLE empleados (
  id       INT PRIMARY KEY,
  nombre   CHAR(30),
  dept     CHAR(20),
  salario  FLOAT
) USING [HEAP | SEQUENTIAL];

DROP TABLE empleados;

CREATE INDEX idx_emp_id ON empleados(id) USING [BTREE | HASH];
DROP INDEX idx_emp_id;

INSERT INTO empleados VALUES (101, 'Ada Lovelace', 'Analytics', 5200.0);
INSERT INTO empleados (id, nombre) VALUES (102, 'Grace Hopper');

SELECT * FROM empleados WHERE id = 101;
SELECT nombre, salario FROM empleados WHERE id >= 100 AND id <= 500;
SELECT * FROM empleados WHERE salario BETWEEN 5000 AND 9000 ORDER BY salario DESC LIMIT 10;
SELECT * FROM empleados WHERE dept IS NOT NULL AND NOT salario < 6000;

DELETE FROM empleados WHERE id = 101;

EXPLAIN SELECT * FROM empleados WHERE id = 101;   -- planifica sin ejecutar

-- Carga masiva: no pasa por el parser, una fila del CSV es una fila de la tabla
COPY empleados FROM 'empleados.csv';
COPY empleados FROM 'datos.tsv' WITH (HEADER FALSE, DELIMITER '\t', NULL 'NA');

-- El tamano de bloque es una opcion de tabla, lo que el Experimento 4 varia
CREATE TABLE grande (id INT PRIMARY KEY, monto FLOAT) WITH (PAGE_SIZE = 8192);
```

`PAGE_SIZE` llega hasta el archivo: los bloques en disco miden exactamente lo
declarado. El adaptador comprueba que la capa fisica acepte el parametro y
rechaza cualquier tamano que el directorio de slots no pueda direccionar.

`COPY` resuelve la ruta dentro de `QE_DATA_DIR` y no deja salir de ahí. Las
cabeceras se emparejan sin distinguir mayúsculas, espacios ni guiones bajos, así
que un CSV de Kaggle con `Employee_ID` alimenta una columna `id`. Las filas que
no coaccionan se rechazan con su número de línea en vez de abortar la carga.

**Tipos:** `INT`, `BIGINT`, `FLOAT`, `DOUBLE`, `BOOL`, `CHAR(n)`, `VARCHAR(n)`, `DATE`.
El formato binario del registro se deriva de los tipos declarados, así que el motor
se adapta a cualquier dataset sin tocar código:

| CREATE TABLE | `record_format` | bytes |
|---|---|---|
| `(id INT, nombre CHAR(30), salario FLOAT)` | `<i30sf` | 38 |
| `(zip INT, lat DOUBLE, lng DOUBLE, estado CHAR(2))` | `<idd2s` | 22 |

**Operadores en `WHERE`:** `=`, `<>`, `!=`, `<`, `<=`, `>`, `>=`, `BETWEEN`,
`IS [NOT] NULL`, `AND`, `OR`, `NOT` y paréntesis. Comparar contra `NULL` da
UNKNOWN, no falso, como manda el estándar.

---

## Cómo elige la ruta de acceso

El planificador parte el `WHERE` en conjunciones, se queda con las *sargables*
(`columna OP constante`), las pliega en un rango por columna, enumera las rutas
disponibles y se queda con la más barata. Lo que la ruta elegida no garantiza
se vuelve a verificar en un `Filter` encima, así que el plan es correcto gane
quien gane.

| Situación | Ruta |
|---|---|
| Igualdad + índice `HASH` o `BTREE` | `IndexScan` |
| Rango + índice `BTREE` | `IndexRangeScan` |
| Igualdad sobre la PK de una tabla `SEQUENTIAL` | `SequentialSearch` (búsqueda binaria) |
| Rango sobre la PK de una tabla `SEQUENTIAL` | `SequentialRangeScan` |
| Todo lo demás (incluido cualquier `OR`) | `SeqScan` |

### El modelo de costos

Todo se cotiza en **bloques transferidos**, que es justo lo que mide el
`DiskCounter`, para poder comparar el estimado contra lo medido.

| Ruta | Bloques |
|---|---|
| `SeqScan` | `P` (páginas de la tabla) |
| `IndexScan` (BTREE) | `h + filas` |
| `IndexScan` (HASH) | `1.2 + filas` |
| `IndexRangeScan` | `h + ⌈filas / 64⌉ + filas` |
| `SequentialSearch` | `⌈log₂(P+1)⌉ + 1` |
| `SequentialRangeScan` | `⌈log₂(P+1)⌉ + ⌈filas / registros_por_pagina⌉` |

El término `+ filas` es una lectura por cada RID alcanzado: la hipótesis
pesimista de índice **no agrupado**. Es lo que hace que un índice pierda contra
el escaneo completo cuando el rango se ensancha, que es exactamente el cruce que
busca el Experimento 3.

La selectividad sale de los mínimos y máximos que el catálogo mantiene por
columna numérica, asumiendo distribución uniforme. Sin esos extremos cae a las
constantes de libro (1/3 para una desigualdad).

Medición real sobre 3 000 filas en 19 páginas:

```
SELECT * FROM viajes WHERE id = 2500;

sin índice   -> SeqScan                        estimado ~19 bloques   medido 19 lecturas
con BTREE    -> IndexScan (h=3)                estimado  ~3 bloques   medido  3 lecturas
```

---

## API

### `POST /api/query`

```json
{ "sql": "SELECT * FROM empleados WHERE id = 101" }
```

```json
{
  "statement": "Select",
  "columns": ["id", "nombre", "dept", "salario"],
  "rows": [[101, "Ada Lovelace", "Analytics", 5200.0]],
  "row_count": 1,
  "affected_rows": 0,
  "message": "",
  "plan": {
    "node": "IndexScan",
    "table": "empleados",
    "index": "idx_emp_id",
    "using": "BTREE",
    "condition": "id = 101",
    "cost": { "estimated_blocks": 3.0, "estimated_rows": 1, "rationale": "..." }
  },
  "plan_text": "-> IndexScan (...)  [~3.0 bloques, ~1 filas]",
  "metrics": {
    "parse_ms": 0.04, "plan_ms": 0.05, "execution_ms": 0.01, "total_ms": 0.10,
    "disk_reads": 3, "disk_writes": 0, "disk_total": 3
  }
}
```

`plan` es el árbol anidado (para dibujarlo) y `plan_text` la versión plana
(para mostrarla tal cual). El panel de métricas del cliente se alimenta de
`metrics`.

### `GET /api/tables`

Devuelve, por tabla: motor, tamaño de página, `record_format`, bytes por
registro, registros por página, filas, páginas, columnas e índices activos.

### `POST /api/tables/load`

```json
{ "table": "viajes", "path": "viajes.csv", "header": true, "delimiter": "," }
```

Carga masiva desde un archivo dentro de `QE_DATA_DIR`. Responde con filas
leídas, insertadas, rechazadas, filas por segundo y el costo en bloques. Es la
vía para preparar los datasets de los experimentos: 20 000 filas entran en
123 escrituras, una por página.

### `POST /api/tables/reorganize`

```json
{ "table": "empleados" }
```

Solo aplica a tablas `SEQUENTIAL`. Responde con cuántos registros se rescataron
del área de overflow, páginas antes y después, fill factor y el costo en bloques.

### Errores

Cualquier fallo del motor sale como **HTTP 400** con forma estable:

```json
{ "error": "se esperaba FROM y se encontro 'FORM' (linea 1, columna 10)",
  "kind": "SQLSyntaxError", "line": 1, "column": 10 }
```

`kind` es `SQLSyntaxError`, `CatalogError`, `TypeMismatchError`, `PlannerError`
o `StorageUnavailableError`. Los errores de sintaxis traen línea y columna para
subrayar el punto exacto en el editor SQL.

---

## Integración con los demás bloques

Aquí está el punto de contacto. El motor depende de dos protocolos declarados en
`queryengine/storage/port.py`, nada más:

```python
class StorageEngine(Protocol):        # BLK 01 + BLK 02
    io: IOCounter
    def create_table(self, schema: TableSchema) -> None: ...
    def drop_table(self, table: str) -> None: ...
    def insert(self, table: str, record: tuple) -> RID: ...
    def fetch(self, table: str, rid: RID) -> tuple | None: ...
    def scan(self, table: str) -> Iterator[tuple[RID, tuple]]: ...
    def delete(self, table: str, rid: RID) -> bool: ...
    def page_count(self, table: str) -> int: ...
    def search_key(self, table: str, key) -> list[tuple[RID, tuple]]: ...
    def range_key(self, table: str, lower, upper) -> Iterator[tuple[RID, tuple]]: ...
    def reorganize(self, table: str) -> ReorganizeReport: ...

class IndexManager(Protocol):         # BLK 03 + BLK 04
    io: IOCounter
    def create_index(self, meta: IndexMeta, schema: TableSchema) -> None: ...
    def drop_index(self, name: str) -> None: ...
    def insert(self, name: str, key, rid: RID) -> None: ...
    def delete(self, name: str, key, rid: RID) -> None: ...
    def search(self, name: str, key) -> list[RID]: ...
    def range_search(self, name: str, lower, upper) -> list[RID]: ...
    def height(self, name: str) -> int: ...
    def bulk_load(self, name: str, entries) -> None: ...
```

Reglas que toda implementación debe cumplir:

1. Un **RID** es el par `(page_id, slot)` y se mantiene estable hasta que el
   registro se borre o la tabla se reorganice.
2. Un **registro** es una tupla de valores Python en el orden de
   `schema.columns`. La conversión a bytes la hace quien escribe la página, con
   el `record_format` que publica el esquema.
3. **Todo bloque transferido incrementa el `IOCounter` inyectado.** Es una única
   instancia compartida entre tablas e índices, para que las cifras de una
   consulta sumen a través de todas las estructuras que tocó.
4. `search_key` y `range_key` solo tienen sentido en tablas `SEQUENTIAL`; en una
   `HEAP` pueden lanzar `NotImplementedError`, el planificador nunca las pide.

Antes de enchufar una implementación hay que pasarla por la suite de
conformidad, que fija lo que el ejecutor asume. Una prueba que falle significa
que el motor devolvería filas incorrectas o reportaría mal el costo:

```python
import pytest
from queryengine.storage.port import IOCounter
from queryengine.testing import StorageEngineContract

class TestMiHeapFile(StorageEngineContract):
    @pytest.fixture
    def store(self, tmp_path):
        return MiHeapFile(IOCounter(), str(tmp_path))
```

Para índices están `IndexManagerContract` (hash) y `RangeIndexContract` (B+).

Los backends disponibles se registran en `queryengine/bootstrap.py`; el parser,
el planificador y el ejecutor no se tocan:

| Backend | Tablas | Índices |
|---|---|---|
| `memory` | sustituto en memoria | sustituto en memoria |
| `disk` | bloques reales de 4 KB vía `storage/` | sustituto en memoria |

`DiskTableStore` importa `Page`, `DiskManager` y `DiskCounter` desde el módulo
de almacenamiento y **no reimplementa nada de eso**: aporta solo el nivel de
tabla (un archivo por tabla, ubicación en inserción, recorrido completo,
eliminación lógica y la serialización tupla ↔ bytes con mapa de nulos). Mantiene
la página de cola en memoria entre inserciones, lo que baja el costo de una
escritura por registro a una por página.

Los sustitutos en memoria **no escriben a disco**: derivan el costo del
`records_per_page` del esquema para ejercitar la telemetría de punta a punta.
Sirven para desarrollar y testear, no para medir.

El estado de la integración y lo que falta está en `INTEGRACION.md` en la raíz.

---

## Estructura

```
queryengine/
  types.py          tipos de columna -> formato struct, coerción de literales
  catalog.py        esquemas, índices y estadísticas (JSON; los datos nunca pasan por aquí)
  errors.py         jerarquía de errores que ve el cliente
  sql/
    tokens.py       vocabulario
    lexer.py        scanner con línea y columna
    ast.py          árbol sintáctico
    parser.py       descenso recursivo
  planner/
    binder.py       resuelve nombres contra el catálogo y coacciona literales
    predicates.py   extracción de predicados sargables y plegado de rangos
    cost.py         modelo analítico en bloques
    plan.py         nodos del plan físico + EXPLAIN
    planner.py      selección de ruta de acceso
  execution/
    expressions.py  evaluación con lógica trivaluada
    executor.py     operadores iteradores
  storage/
    port.py         LOS PUERTOS: contrato con los demás bloques
    memory.py       sustitutos en memoria para desarrollo
    blk01.py        carga el módulo de almacenamiento físico
    diskstore.py    tablas sobre bloques reales de 4 KB
    codec.py        tupla <-> bytes, con mapa de nulos
  testing/
    contract.py     suite de conformidad de los puertos
  loader.py         carga masiva de CSV
  cli.py            cliente SQL de consola
  engine.py         fachada: parse -> plan -> ejecuta -> reporta costo
  bootstrap.py      cableado desde variables de entorno
  api/app.py        FastAPI
```

Fases, en orden: **lexer → parser → binder → planner → executor**. El binder es
la frontera entre lo sintáctico y lo semántico: antes de él `id = '101'` es una
comparación contra una cadena; después es contra el entero `101`.

---

## Configuración

| Variable | Por defecto | Para qué |
|---|---|---|
| `QE_STORAGE_BACKEND` | `memory` | `memory` (sustituto) o `disk` (bloques reales) |
| `QE_CATALOG_PATH` | `data/catalog.json` | Catálogo persistente; `:memory:` lo desactiva |
| `QE_DATA_DIR` | — | Directorio del que `COPY` puede leer |
| `QE_TABLE_DIR` | `data/tables` | Dónde el backend de disco guarda un `.bin` por tabla |
| `QE_BLK01_PATH` | `../../storage` | Módulo de almacenamiento físico a enlazar |

---

## Notas

- El catálogo se guarda en JSON. Es **metadata**, no datos de usuario: ninguna
  página, registro ni índice pasa por ahí. La prohibición de serializadores de
  alto nivel aplica al almacenamiento físico, que vive detrás de los puertos.
- Las cargas masivas van por `COPY`, que no pasa por el parser. Un `INSERT` de
  3 000 tuplas cuesta ~55 ms solo de lexer; a 500 000 filas el parser, y no el
  disco, sería lo que mide el Experimento 1.
- Los cuatro experimentos se corren con
  `python benchmarks/experiments.py --backend disk`, que deja un CSV por
  experimento y un `RESUMEN.md` listo para el informe.
- `EXPLAIN` planifica sin ejecutar: sirve para comparar el costo estimado contra
  el medido sin pagar la consulta.
