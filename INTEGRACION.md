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

### Experimento 1 — inserción masiva de 500 000 registros

| Estructura | Tiempo | Escrituras |
|---|---|---|
| Heap | 4,78 s | 13 158 |
| Sequential (con reorganización) | 9,08 s | 31 016 |
| Heap + árbol B+ | 183,82 s | 522 014 |
| Heap + hash extensible | 61,62 s | 1 023 922 |

El hallazgo: **mantener un índice cuesta más que guardar los datos**. El Heap
escribe una página por bloque lleno; cada índice, en cambio, baja su nodo o su
bucket a disco en *cada* inserción. El árbol es 38 veces más lento y el hash
escribe 78 veces más bloques. Si ambos bufferizaran el camino que acaban de
recorrer entre inserciones consecutivas, la diferencia se reduciría mucho.

### Experimento 2 — 1 000 búsquedas puntuales sobre N = 100 000

| Ruta | Lecturas (media) | Desviación |
|---|---|---|
| Full Scan (Heap) | 2 631 | 0 |
| Búsqueda binaria (Sequential) | 11,85 | — |
| Árbol B+ | 5 | 0 |
| Hash extensible | 2 | 0 |

### Experimento 3 — rangos con selectividad variable

| Selectividad | Árbol B+ | Sequential | Full Scan |
|---|---|---|---|
| 0,1 % | 105 | **17** | 2 631 |
| 1 % | 1 010 | **48** | 2 631 |
| 5 % | 2 631 (vuelve a SeqScan) | **192** | 2 631 |
| 10 % | 2 631 (vuelve a SeqScan) | **371** | 2 631 |
| 25 % | 2 631 (vuelve a SeqScan) | **906** | 2 631 |

El Sequential File gana en todas las selectividades, y la razón es que está
**agrupado**: sus registros viven físicamente en orden de clave, así que un
rango son páginas contiguas. El árbol B+ es un índice secundario, y cada RID
que alcanza cuesta una lectura suelta; pasado el 1 % eso supera el costo de
leer la tabla entera y el planificador vuelve al escaneo completo — el cruce
que el experimento busca.

### Experimento 4 — sensibilidad al tamaño de bloque

| B | Registros por página |
|---|---|
| 1 024 | 9 |
| 2 048 | 19 |
| 4 096 | 38 |
| 8 192 | 77 |

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
