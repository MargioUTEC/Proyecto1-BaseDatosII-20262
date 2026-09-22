# Integración de los módulos

Estado de la unión entre el almacenamiento físico (`storage/`) y el motor de
consultas (`backend/query-engine/`), y lo que falta para cerrar el Entregable 1.

## Cómo se conectan hoy

El motor no abre archivos. Depende de dos protocolos declarados en
`backend/query-engine/queryengine/storage/port.py`:

| Puerto | Qué resuelve | Implementación actual |
|---|---|---|
| `StorageEngine` | tablas: insert, fetch, scan, delete, reorganize | `DiskTableStore` sobre `Page` + `DiskManager` |
| `IndexManager` | índices: search, range_search, height | `DiskIndexStore` (B+) y `DiskHashIndex` (hash), con ruteo |

`DiskTableStore` (`storage/diskstore.py`) enchufa la capa física existente contra
el primer puerto. **Usa `Page`, `DiskManager` y `DiskCounter` tal como están**,
importándolos desde `storage/`: no reimplementa el layout de página ni los
bloques. Lo único que aporta es el nivel de tabla que la capa física deja
abierto:

- un archivo `.bin` por tabla, de modo que el `page_id` de un RID indexa ese archivo
- ubicación en inserción: llenar la página de cola, si no cabe abrir otra
- el recorrido completo de la tabla, página por página
- eliminación lógica poniendo en cero la longitud del slot, para que los RID sobrevivan
- serialización tupla ↔ bytes con el `record_format` que deriva del `CREATE TABLE`,
  más un mapa de nulos de un bit por columna

Cualquier arreglo en `storage/page.py` o `storage/disk_management.py` llega al
motor sin tocar nada del lado del motor.

## Verificado

Contra bloques reales de 4 KB, 20 000 filas:

```
COPY viajes FROM 'viajes.csv'   ->  123 escrituras, 123 páginas, 217k filas/s
SELECT ... WHERE id = 19999     ->  SeqScan   123 lecturas   (estimado 123)
  + CREATE INDEX ... USING BTREE ->  IndexScan   3 lecturas   (estimado 4)
```

El costo estimado por el planificador coincide con el medido por el
`DiskCounter`.

Los cuatro experimentos corren sobre disco a la escala que pide el enunciado
(`N` hasta 500 000). Resultados en `benchmarks/results/`:

```
python benchmarks/experiments.py --backend disk        # ~3 min
```

| Experimento | Resultado |
|---|---|
| 1 · Inserción masiva | Heap 2,05 s y 3 068 escrituras para 500 000 filas; con árbol B+, 180 s y 511 924 |
| 2 · Igualdad (N=100k) | Full Scan 613 lecturas · B+ 5 · Hash 2 |
| 3 · Selectividad | El cruce cae entre 0,1 % (IndexRangeScan, 105) y 1 % (SeqScan, 613) |
| 4 · Tamaño de bloque | Fan-out 40 / 81 / 163 / 326 registros por página para 1–8 KB |

El costo de inserción con índice es el hallazgo más fuerte del Experimento 1:
mantener el árbol B+ multiplica por 88 el tiempo y por 167 las escrituras. La
causa es que `_write_node` baja el nodo a disco en **cada** inserción; el Heap,
en cambio, mantiene la página de cola en memoria y escribe una vez por página.
Si el árbol bufferizara el camino raíz-hoja entre inserciones consecutivas, esa
diferencia se reduciría mucho — vale la pena decirlo en el informe.

La fila `Hash` sigue saliendo del sustituto en memoria.

## Suite de conformidad

Antes de enchufar una implementación nueva, hay que pasarla por el contrato.
Son 25 pruebas que fijan lo que el ejecutor asume; una que falle significa que
el motor devolvería filas incorrectas o reportaría mal el costo.

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

## Lo que falta

### 1. `heap_file.py` y `sequential_file.py` traen el esquema fijo

Ambos declaran

```python
RECORD_FORMAT = "<i 30s 20s f i i"   # id, nombre, dept, salario
```

o sea solo almacenan esa tabla de empleados. El dataset que elegimos
(`customers`, con 12 columnas de texto y una fecha) no cabe ahí, y tampoco
cabría ningún otro: el motor deriva el formato binario del `CREATE TABLE`, que
es lo que lo hace independiente del dataset.

Por eso el área ordenada está implementada de forma genérica en
`DiskTableStore`, sobre la misma página física de `storage/page.py`:

- **Área principal**: un `.bin` con los registros ordenados por clave primaria,
  recorrido con descenso binario sobre las páginas.
- **Área de desbordamiento**: un `.ovf` aparte al que van las inserciones
  nuevas; se recorre linealmente y se fusiona al reorganizar. Sus páginas se
  direccionan con `page_id` negativo, así un RID sigue nombrando un solo
  registro sin necesitar un tercer campo.
- **`reorganize()`**: mezcla ambas áreas, reescribe la principal en orden con
  fill factor 0,75 y vacía el overflow.

`storage/sequential_file.py` queda como entregable propio del bloque; para que
el motor lo use tendría que derivar el formato del registro del esquema en vez
de tenerlo escrito.

### 2. Nada más bloquea

`search_key`, `range_key` y `reorganize` todavía no tienen implementación en
disco, así que una tabla `USING SEQUENTIAL` reporta el hueco en vez de fingir
estar ordenada. Falta el área principal ordenada, el overflow encadenado y la
reorganización con fill factor 70–80 %. El planificador ya emite
`SequentialSearch` y `SequentialRangeScan` cuando corresponde.

### Índices: árbol B+ y hash extensible, ambos en disco

`DiskIndexStore` enchufa `BPlusTree` al puerto `IndexManager`, con un archivo
`.idx` por índice y su propio `DiskManager`, de modo que sus transferencias
entran en el mismo `DiskCounter` que las de la tabla.

El árbol no cubre todos los casos, así que `RoutingIndexStore` decide por índice
a dónde va y lo deja escrito en `GET /api/tables`:

| Índice | Va a | Por qué |
|---|---|---|
| `BTREE` sobre `INT` que es `PRIMARY KEY` | árbol B+ en disco | caso completo |
| `HASH` sobre `INT` o `BIGINT` | hash extensible en disco | maneja claves repetidas |
| `BTREE` sobre otra columna | sustituto en memoria | el árbol empaqueta claves como `<i` y trata las repetidas como una sola |
| cualquiera sobre columna no entera | sustituto en memoria | ambas estructuras indexan enteros |

El hash extensible pasa el contrato de índices **completo**, duplicados
incluidos. El árbol B+ pasa 8 de 10.

Las dos limitaciones del árbol están fijadas como `xfail` estricto en
`tests/test_contract_bplus.py`: si alguien agrega soporte de duplicados, el test
falla avisando que ya se puede quitar la marca.

**Para que el árbol cubra los índices secundarios** hacen falta dos cosas:
claves de más de 4 bytes (hoy `struct` usa `<i`, así que `FLOAT`, `CHAR` y
`BIGINT` quedan fuera) y claves repetidas. Hoy `_insert_rec` devuelve
`(None, None)` cuando la clave ya existe, o sea descarta la entrada sin avisar;
el adaptador lo convierte en error en vez de perder filas en silencio.

### El servicio HTTP de `storage/` no está en el camino de datos

`storage/main.py` expone las páginas por HTTP con los registros en base64. Es
útil para inspeccionar bloques y para la demo, pero el motor **no** lo usa: un
escaneo de 500 000 filas serían cientos de miles de llamadas HTTP y la latencia
de red se comería la medición de I/O. El motor importa el módulo en proceso y
ambos se despliegan juntos con `docker compose`.

## Cambios hechos sobre `storage/`

Tres cambios sobre la capa física, todos aditivos y retrocompatibles
(`storage/test_storage.py` sigue pasando sin tocarlo):

1. **`PAGE_SIZE` pasó de constante a parámetro.** `Page(page_id, ..., page_size=PAGE_SIZE)`
   y `DiskManager(db_path, page_size=PAGE_SIZE)` conservan 4096 por defecto. Sin
   esto el Experimento 4 era imposible: el enunciado exige medir con
   `B ∈ [1024, 2048, 4096, 8192]` y la capa quedaba fijada en 4096.
   El adaptador detecta si el módulo acepta el parámetro, así que también
   funciona contra la versión anterior — solo que ahí no puede variar `B`.
2. **`allocate_page` pasó de modo `a+b` a `r+b`.** En POSIX el modo append ignora
   el `seek` y escribe siempre al final: funcionaba solo porque el destino
   coincidía con el final del archivo, y habría ocultado cualquier error de
   offset.
3. **`storage/Dockerfile`** estaba vacío (0 bytes); ahora levanta el servicio.

Queda un detalle sin tocar: `SLOT_FORMAT = "<HH"` limita offset y longitud a
65 535, así que una página mayor a 64 KB rompería el directorio de slots. No
estorba a 4 u 8 KB, y el adaptador rechaza cualquier `PAGE_SIZE` por encima de
ese límite en vez de corromper la página.

## Levantar todo

```bash
docker compose up --build      # storage en :8000, motor en :8001
```

O solo el motor, en local:

```bash
cd backend/query-engine
pip install -r requirements-dev.txt
pytest                                     # 185 pruebas
QE_STORAGE_BACKEND=disk python -m queryengine
```
