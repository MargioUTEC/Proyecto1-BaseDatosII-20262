# Integración de los módulos

Estado de la unión entre el almacenamiento físico (`storage/`) y el motor de
consultas (`backend/query-engine/`), y lo que falta para cerrar el Entregable 1.

## Cómo se conectan hoy

El motor no abre archivos. Depende de dos protocolos declarados en
`backend/query-engine/queryengine/storage/port.py`:

| Puerto | Qué resuelve | Quién lo implementa |
|---|---|---|
| `StorageEngine` | tablas: insert, fetch, scan, delete, reorganize | BLK 01 + BLK 02 |
| `IndexManager` | índices: search, range_search, height | BLK 03 + BLK 04 |

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
| 1 · Inserción masiva | Heap escribe una página por bloque: 3 068 escrituras para 500 000 filas |
| 2 · Igualdad (N=100k) | Full Scan 613 lecturas · B+ 4 · Hash 2 |
| 3 · Selectividad | El cruce cae entre 0,1 % (IndexRangeScan, 105) y 1 % (SeqScan, 613) |
| 4 · Tamaño de bloque | Fan-out 40 / 81 / 163 / 326 registros por página para 1–8 KB |

Los números de índice del Experimento 1 salen del sustituto en memoria, que
cuesta una escritura por clave; un árbol B+ real con buffer escribirá mucho
menos. Hay que rehacer esa fila cuando llegue BLK 03.

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

### 1. Sequential File — BLK 02

`search_key`, `range_key` y `reorganize` todavía no tienen implementación en
disco, así que una tabla `USING SEQUENTIAL` reporta el hueco en vez de fingir
estar ordenada. Falta el área principal ordenada, el overflow encadenado y la
reorganización con fill factor 70–80 %. El planificador ya emite
`SequentialSearch` y `SequentialRangeScan` cuando corresponde.

### 2. Índices en disco — BLK 03 y BLK 04

Mientras el árbol B+ y el hash dinámico no existan, el backend `disk` combina
tablas en disco con **índices en memoria**, para que las rutas de acceso se
puedan ejercitar de punta a punta. Los números de índice del Experimento 1
salen de ese sustituto, no de un árbol real: hay que rehacerlos cuando lleguen.

### 3. El servicio HTTP de `storage/` no está en el camino de datos

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
pytest                                     # 175 pruebas
QE_STORAGE_BACKEND=disk python -m queryengine
```
