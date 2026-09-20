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

pytest                                              # 92 tests
uvicorn queryengine.api.app:app --reload --port 8001
```

Con Docker:

```bash
docker compose up --build        # queda en http://localhost:8001
```

Comprobación en una línea:

```bash
curl -s -XPOST localhost:8001/api/query -H 'Content-Type: application/json' \
  -d '{"sql":"CREATE TABLE t (id INT PRIMARY KEY, nombre CHAR(30)) USING HEAP"}'
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
```

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

Para enchufar la implementación real basta registrarla en
`queryengine/bootstrap.py`; no se toca el parser, el planificador ni el ejecutor:

```python
if settings.backend == "disk":
    from storage_engine import DiskTableStore, DiskIndexStore   # módulo del compañero
    storage = DiskTableStore(io, data_dir=settings.data_dir)
    indexes = DiskIndexStore(io, data_dir=settings.data_dir)
```

Mientras tanto corre `MemoryTableStore` / `MemoryIndexStore`, que **no escriben a
disco**: derivan el costo del `records_per_page` del esquema para ejercitar la
telemetría de punta a punta. Sirven para desarrollar y testear, **no para medir**.
Los benchmarks van contra los adaptadores reales.

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
    memory.py       adaptador de desarrollo
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
| `QE_STORAGE_BACKEND` | `memory` | Adaptador de almacenamiento |
| `QE_CATALOG_PATH` | `data/catalog.json` | Catálogo persistente; `:memory:` lo desactiva |

---

## Notas

- El catálogo se guarda en JSON. Es **metadata**, no datos de usuario: ninguna
  página, registro ni índice pasa por ahí. La prohibición de serializadores de
  alto nivel aplica al almacenamiento físico, que vive detrás de los puertos.
- Para cargas masivas conviene un cargador que llame a `StorageEngine.insert`
  directamente. Parsear un `INSERT` de 3 000 tuplas cuesta ~55 ms de lexer; a
  500 000 filas eso domina el tiempo y contaminaría el Experimento 1.
- `EXPLAIN` planifica sin ejecutar: sirve para comparar el costo estimado contra
  el medido sin pagar la consulta.
