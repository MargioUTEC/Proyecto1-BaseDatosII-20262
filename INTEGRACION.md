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
`DiskCounter`. Los cuatro experimentos corren con
`python benchmarks/experiments.py --backend disk`.

## Suite de conformidad

Antes de enchufar una implementación nueva, hay que pasarla por el contrato.
Son 23 pruebas que fijan lo que el ejecutor asume; una que falle significa que
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

### 1. `PAGE_SIZE` tiene que ser un parámetro — bloquea el Experimento 4

`storage/config.py` define `PAGE_SIZE = 4096` como constante de módulo, y
`page.py` la lee al importarse. El enunciado exige correr con
`B ∈ [1024, 2048, 4096, 8192]` y medir el efecto en el fan-out, la altura del
árbol y el total de I/O. Hoy solo se puede medir 4096:

```
E4 B= 1024  omitido: la capa fisica esta compilada para 4096 B
E4 B= 4096    163 reg/pag
```

Hace falta que `Page` y `DiskManager` reciban el tamaño en el constructor
(`Page(page_id, page_size=...)`), con 4096 por defecto. El motor ya lo soporta:
`CREATE TABLE ... WITH (PAGE_SIZE = 8192)` y valida que coincidan.

### 2. Sequential File — BLK 02

`search_key`, `range_key` y `reorganize` todavía no tienen implementación en
disco, así que una tabla `USING SEQUENTIAL` reporta el hueco en vez de fingir
estar ordenada. Falta el área principal ordenada, el overflow encadenado y la
reorganización con fill factor 70–80 %. El planificador ya emite
`SequentialSearch` y `SequentialRangeScan` cuando corresponde.

### 3. Índices en disco — BLK 03 y BLK 04

Mientras el árbol B+ y el hash dinámico no existan, el backend `disk` combina
tablas en disco con **índices en memoria**, para que las rutas de acceso se
puedan ejercitar de punta a punta. Los números de índice del Experimento 1
salen de ese sustituto, no de un árbol real: hay que rehacerlos cuando lleguen.

### 4. El servicio HTTP de `storage/` no está en el camino de datos

`storage/main.py` expone las páginas por HTTP con los registros en base64. Es
útil para inspeccionar bloques y para la demo, pero el motor **no** lo usa: un
escaneo de 500 000 filas serían cientos de miles de llamadas HTTP y la latencia
de red se comería la medición de I/O. El motor importa el módulo en proceso y
ambos se despliegan juntos con `docker compose`.

### 5. Detalles menores de la capa física

- `allocate_page` abre el archivo en modo `a+b` y luego hace `seek`; en POSIX el
  modo append ignora el `seek` y siempre escribe al final. Funciona porque el
  destino coincide con el final, pero es frágil: conviene `r+b`.
- `SLOT_FORMAT = "<HH"` limita offset y longitud a 65 535, así que una página
  mayor a 64 KB rompería el directorio de slots. No estorba a 4 u 8 KB.
- El motor evita `allocate_page` al abrir una página nueva, porque escribe un
  bloque de ceros que el flush sobrescribe de inmediato: eran dos escrituras por
  página en vez de una.

## Levantar todo

```bash
docker compose up --build      # storage en :8000, motor en :8001
```

O solo el motor, en local:

```bash
cd backend/query-engine
pip install -r requirements-dev.txt
pytest                                     # 165 pruebas
QE_STORAGE_BACKEND=disk python -m queryengine
```
