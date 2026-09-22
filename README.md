# Gestor de Bases de Datos Multimodal — Entregable 1

Proyecto del curso **Base de Datos II (CS2042)** · UTEC · 2026-II

Mini-gestor de bases de datos construido desde cero sobre memoria secundaria:
páginas binarias de tamaño fijo, índices propios, un parser SQL con
planificador de consultas, y un cliente web que muestra el costo físico exacto
de cada consulta.

## Levantar el sistema

```bash
git clone https://github.com/MargioUTEC/Proyecto1-BaseDatosII-20262.git
cd Proyecto1-BaseDatosII-20262
./scripts/fetch_dataset.sh        # dataset de customers (no versionado)
docker compose up --build
```

| Servicio | Puerto | Qué es |
|---|---|---|
| **Consola SQL** | <http://localhost:8080> | cliente web, el punto de entrada |
| Motor de consultas | 8001 | parser, planificador, ejecutor y API REST |
| Almacenamiento físico | 8000 | páginas, bloques y telemetría de E/S |
| Hashing dinámico | 8002 | servicio propio del índice extensible |

En la consola, el desplegable **insertar ejemplo…** trae las sentencias listas:
crear la tabla, cargar el CSV, crear los índices y las consultas de la demo.

## Arquitectura

```
frontend/            cliente web: catálogo, editor SQL, resultados y telemetría
backend/query-engine/ parser SQL -> planificador -> ejecutor, API REST y CLI
storage/             página ranurada, DiskManager, DiskCounter, árbol B+
hashing_dinamico/    hash extensible en disco
benchmarks/          los cuatro experimentos del informe y sus resultados
data/                el dataset (se descarga, no se versiona)
```

El motor no abre archivos: depende de dos puertos declarados en
`backend/query-engine/queryengine/storage/port.py`, y las estructuras físicas se
enchufan detrás. `INTEGRACION.md` explica cómo encajan las piezas, qué verifica
la suite de conformidad y qué falta.

## Qué hace

```sql
CREATE TABLE customers ("Index" INT PRIMARY KEY, "Country" CHAR(56))
  USING [HEAP | SEQUENTIAL] WITH (PAGE_SIZE = 8192);

COPY customers FROM 'customers-100000.csv';
CREATE INDEX ix ON customers("Index") USING [BTREE | HASH];

SELECT * FROM customers WHERE "Index" = 77777;
SELECT * FROM customers WHERE "Index" BETWEEN 1 AND 500;
EXPLAIN SELECT * FROM customers WHERE "Index" = 42;
```

El planificador elige la ruta de acceso cotizando cada candidata en bloques
transferidos, y el cliente muestra el costo estimado junto al medido por el
`DiskCounter`. Sobre 100 000 clientes en 8 334 páginas de 4 KB:

| Consulta | Ruta | Estimado | Medido |
|---|---|---|---|
| `"Index" = 77777` sin índice | SeqScan | 8 334 bloques | 8 334 lecturas · 492 ms |
| `"Index" = 77777` con hash | IndexScan | 2,2 bloques | 2 lecturas · 0,24 ms |
| `BETWEEN 1 AND 500` con B+ | IndexRangeScan | 511 bloques | 506 lecturas · 14 ms |

## Experimentos

```bash
python benchmarks/experiments.py --backend disk     # ~13 min, N hasta 500 000
```

Deja un CSV por experimento y un `RESUMEN.md` en `benchmarks/results/`, ya
corridos y listos para el informe.

## Pruebas

```bash
cd backend/query-engine
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest          # 205 pruebas
```

Incluye una suite de conformidad reutilizable: cualquier implementación de
almacenamiento o de índices se valida contra el contrato antes de enchufarla.
