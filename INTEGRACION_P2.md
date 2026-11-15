# Integración del Entregable 2

Cómo encajan los cuatro bloques y cómo levantar el sistema completo. El
documento del P1 (`INTEGRACION.md`) sigue valiendo para el almacenamiento, el
árbol B+ y el hashing; esto cubre lo que se añadió encima.

## Quién aporta qué

| Bloque | Dónde vive | Cómo entra al motor |
|---|---|---|
| **A** espacial (Margiory) | `rtree.py` en la raíz | `storage/diskrtree.py`, registrado como candidato en `bootstrap` |
| **B** textual (Mauricio) | paquete `textual/` en la raíz | `storage/diskinverted.py` más el puente `textsearch.py` |
| **C** vectorial (Franc) | `vectorial/`, servicio aparte en el puerto 8003 | `storage/diskvector.py` más el puente `vectorsearch.py`, por HTTP |
| **D** vertebral (Nicolás) | `sql/`, `planner/`, `execution/`, `api/`, `frontend/` | es el motor |

Los bloques A y B corren **dentro** del proceso del motor, importados desde la
raíz del repositorio. El bloque C corre **fuera**, como servicio, por dos
razones: sus vectores los produce un modelo que arrastra opencv y onnxruntime, y
meter eso en la imagen del motor la multiplica de tamaño; y porque es el
contrato que el propio bloque C publicó.

## La consulta vectorial

La tabla no guarda los 128 flotantes de cada rostro. Guarda la ruta de la
imagen, que es la referencia con la que el IVF resuelve su vector. Es la misma
idea con la que el R-Tree indexa `POINT(lon lat)` sobre una columna de texto: la
tabla guarda la referencia y la estructura guarda la geometría.

```sql
CREATE TABLE rostros (
  id        INT PRIMARY KEY,
  lote      CHAR(8),
  embedding VARCHAR(40)
) USING HEAP;

CREATE INDEX ix_rostros_ivf ON rostros (embedding)
  USING IVF WITH (mode = 'face', nprobe = 4);

SELECT id, lote, embedding <=> '[0.12, -0.34, ...]' AS similitud
FROM rostros
WHERE id < 500
ORDER BY similitud DESC
LIMIT 5;
```

### La decisión entre pre y post filtering

Ante un filtro relacional más un ranking vectorial, el planificador construye
las dos rutas y se queda con la barata:

| Estrategia | Nodo | Cuándo gana |
|---|---|---|
| Pre-filtering | `IVFRefine` sobre la vía relacional | el filtro deja menos filas de las que el IVF pediría como candidatos, así que cuesta lo que cuesta el filtro |
| Post-filtering | `IVFScan` con un `Filter` encima | el filtro es débil, así que poda más el índice |
| Ranking directo | `IVFScan` | no hay filtro relacional |

La que corrió queda escrita en el nodo del plan y se ve en el cliente.

Un detalle que costó encontrar: la decisión no puede mirar las filas que reporta
el plan relacional, porque un `SeqScan` con un `Filter` encima sigue diciendo
que devuelve la tabla entera. Lo que decide es la selectividad del predicado,
que se calcula aparte sobre los rangos sargables.

## Levantar todo

### Con Docker, en un paso

```bash
docker compose up --build
```

Deja cinco servicios arriba: `storage` (8000), `query-engine` (8001), `hashing`
(8002), `vectorial` (8003) y `frontend` (8080). El cliente habla con un solo
origen, porque nginx enruta `/api/` al motor.

### A mano, para desarrollar

```bash
# 1. el bloque vectorial, con el dataset ya descargado
cd vectorial
python main.py build --data ../data/ffhq --limit 0
python main.py serve

# 2. el motor
cd backend/query-engine
uvicorn queryengine.api.app:app --port 8001

# 3. la tabla de rostros, enlazada al índice del bloque C
python scripts/cargar_rostros.py
```

La aplicación queda en `frontend/identificacion.html` y la consola SQL en
`frontend/index.html`.

## El dataset

**FFHQ**, carpetas `00000` a `13000`, 14 000 rostros de 1024x1024.

```bash
python scripts/descargar_ffhq.py --destino data/ffhq
```

El script es reanudable: salta las carpetas que ya están completas. Va lento
porque los archivos sueltos de esa unidad compartida no tienen enlace público y
la única vía es pedir la carpeta entera. Si hay prisa, bajarlo desde el
navegador o con Google Drive para escritorio y dejarlo en `data/ffhq/` sale
igual de bien: el resto no distingue de dónde vino.

El índice del bloque C se construye con lo que haya en disco, así que se puede
trabajar con una parte y rehacerlo cuando esté completo.

## Dependencias que hacían falta y no estaban declaradas

- `nltk`, que el bloque B necesita para el stemming. Sin él, tres de sus
  pruebas fallaban con un aviso de que indexaba sin stemming.
- `python-multipart`, que FastAPI necesita para aceptar la subida de la foto.
  Sin él, el fallo aparecía recién al importar los tests.
- `rtree.py` no se copiaba a la imagen del motor, así que el R-Tree funcionaba
  en local y no en Docker.

## Modelos del bloque C

La primera construcción del índice descarga YuNet y SFace (39 MB). Si falla por
verificación de certificado, que es lo que pasa en macOS con Python reciente,
basta con bajarlos a mano:

```bash
mkdir -p data/indices_c/models && cd data/indices_c/models
curl -LO https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx
curl -LO https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx
```
