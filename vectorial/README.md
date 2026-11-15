# vectorial/ — Bloque C (Franc): biométrico y vectorial

Servicio independiente (mismo patrón que `hashing_dinamico/`): embeddings faciales (YuNet + SFace),
BoVW con SIFT, **IVF en disco** y Filter-and-Refine. Experimentos **7** (IVF de rostros, variando `nprobe`)
y **8** (BoVW, variando palabras de consulta).

## Archivos

| Archivo | Qué hace |
|---|---|
| `config.py` | Rutas, parámetros por defecto, modelos, valores de los experimentos |
| `imagenes.py` | Leer, redimensionar, listar dataset, normalizar |
| `modelos.py` | Descarga de YuNet/SFace y `FaceEmbedder` |
| `bovw.py` | SIFT, vocabulario (K-Means), histogramas, TF-IDF |
| `postings.py` | Guardar/leer posting lists en disco (`.bin` + offsets) |
| `construccion.py` | `build()`: crea todos los índices |
| `indice.py` | `VectorIndex`: Filter (candidatos) + Refine (coseno) + búsqueda exhaustiva |
| `benchmark.py` | Experimentos 7 y 8 → CSV en `benchmarks/results/` |
| `api.py` | API FastAPI (contrato para el planificador) |
| `main.py` | CLI: `build`, `search`, `benchmark`, `serve`, `info`, `download-models` |
| `web/index.html` | Demo de búsqueda por imagen (la sirve `api.py` en `/`) |

## Uso (desde esta carpeta)

```powershell
pip install -r requirements.txt
python main.py build --data ..\data\lfw --limit 100      # prueba rápida
python main.py build --data ..\data\lfw --limit 0        # todo el dataset
python main.py search --image ..\data\lfw\Persona\foto.jpg --mode face --k 5
python main.py benchmark --queries 20 --k 5              # crea experimento7_* y experimento8_*
python main.py serve                                      # http://127.0.0.1:8003
```

El índice se guarda en `data/indices_c/` y los CSV en `benchmarks/results/`
(se cambian con `VECTORIAL_INDEX` y `VECTORIAL_RESULTS`).
La primera vez que se indexan rostros se descargan ~39 MB de modelos; si falla por "SHA256 inválido",
usa `VECTORIAL_VERIFICAR_SHA=0`.

## Contrato HTTP (para Nicolás)

- `GET /health`
- `GET /api/status` → `{images, faces, method, root, dataset_available}`
- `POST /api/search` (multipart: `file`, `mode` = `face|bovw`, `k`, `nprobe`, `query_words`, `max_candidates`)
  → `{results:[{id,path,similarity}], candidates, collection_size, selectivity, search_ms, total_ms}`
- `GET /api/image/{id}`

## Cambios en el resto del repo

Agregar al `.gitignore` de la raíz:

```
data/lfw/
data/indices_c/
```

Servicio sugerido para `docker-compose.yml` (ajusta nombres a tu compose):

```yaml
  vectorial:
    build: ./vectorial
    ports: ["8003:8003"]
    volumes:
      - ./data/lfw:/data/lfw:ro
      - ./data/indices_c:/data/indices_c
      - ./benchmarks/results:/results
```
