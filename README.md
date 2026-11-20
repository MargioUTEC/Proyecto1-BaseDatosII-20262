# Proyecto final BD2
## Avance – Margio

### 1. Bloque espacial: R-Tree

Se implementó un índice R-Tree multidimensional con almacenamiento persistente en disco y páginas de 4 KiB. Permite realizar consultas espaciales por ventana, radio y k-NN (vecinos más cercanos), además de contabilizar las lecturas y escrituras físicas.

### 2. Integración con el gestor

Se integró el R-Tree al motor SQL, permitiendo crear índices espaciales sobre columnas que almacenan puntos geográficos (`POINT(lon lat)`). El motor reconoce las consultas espaciales y utiliza el índice cuando está disponible, manteniendo también la opción de escaneo secuencial para comparar resultados.

Además, se incorporó un visor de mapas en la interfaz web, desde donde se pueden seleccionar operaciones espaciales, ingresar coordenadas y visualizar los resultados y las lecturas de disco.

### 3. Validación

Se realizaron pruebas para comparar los resultados del R-Tree con búsquedas por fuerza bruta y escaneo secuencial. También se verificó la persistencia del índice al cerrar y volver a abrir el archivo, así como la coincidencia de resultados en consultas por radio y k-NN.

### 4. Resultados experimentales

Se evaluó el rendimiento con datos sintéticos de hasta 100 000 puntos y consultas de distintas dimensiones.

**Consultas geográficas (2D):**
- El R-Tree obtuvo mejores tiempos y menos lecturas que el escaneo secuencial.
- Para 100 000 puntos, una consulta k-NN con k=20 tomó 2,342 ms con R-Tree frente a 127,930 ms con escaneo secuencial.
- También se observó una reducción importante en las páginas leídas.

**Efecto de la dimensionalidad:**
- En 2D y 4D, el R-Tree presentó mejores tiempos que el escaneo secuencial.
- A partir de 16 dimensiones, el escaneo secuencial resultó más eficiente.
- Esto ocurre porque, al aumentar las dimensiones, los MBR pierden capacidad para descartar regiones y el árbol necesita recorrer más páginas.

### 5. Principales archivos implementados

- `rtree.py`: implementación del índice, persistencia y consultas.
- `diskrtree.py`: integración con el almacenamiento del motor.
- `spatial.py`, `planner/`, `execution/` y `api/`: procesamiento y ejecución de consultas espaciales.
- `frontend/`: visor de mapas en la interfaz web.
- `test_rtree.py`: pruebas de validación.
- `benchmarks/experimento5_espacial.py`: experimentos y generación de resultados.

## Módulo textual y datos

### 1. Índice invertido en disco

Se implementó un índice invertido persistido en disco con páginas de 4 KiB, separado en tres archivos binarios: `vocab.dic` (términos, df y puntero a su lista), `postings.inv` (listas de posteo ⟨doc_id, tf⟩ ordenadas) y `docs.bin` (RID y norma de cada documento). El índice se construye con el esquema SPIMI: los documentos se invierten en bloques en memoria, cada bloque se guarda ordenado en disco y al final se mezclan todos. Ubicar un término cuesta una sola lectura, y leer su lista solo las páginas que ocupa. Las lecturas y escrituras físicas se contabilizan en el contador de I/O.

Los textos pasan por un preprocesamiento que los convierte a minúsculas, quita tildes, elimina stopwords en español y reduce cada palabra a su raíz con Snowball (nltk).

### 2. Consultas y ranking

Se implementaron consultas booleanas AND, OR y AND-NOT, que se resuelven mezclando listas ordenadas en tiempo lineal. También se implementó el ranking por relevancia TF-IDF con similitud de coseno, que devuelve los K documentos más relevantes. Las normas de los documentos se calculan al construir el índice, por lo que el ranking no necesita leer la tabla.

### 3. Integración con el gestor

Se integró el índice al motor SQL:

```sql
CREATE INDEX ix_cuerpo ON expedientes(cuerpo) USING INVERTED WITH (scoring = 'TFIDF_COSINE');

SELECT id, COSINE_SCORE() AS relevancia FROM expedientes
 WHERE cuerpo MATCH 'verificacion AND rostro AND NOT mascarilla'
 ORDER BY relevancia DESC LIMIT 10;
```

El planificador usa el índice cuando existe (`InvertedIndexScan`) y, si no, recorre la tabla completa (`FullTextScan`) para comparar resultados. En consultas que combinan texto con otras condiciones, primero aplica la búsqueda textual y luego filtra el resto. El plan muestra por separado las lecturas del índice y las de la tabla. La consola web incluye consultas de ejemplo para la búsqueda textual.

### 4. Datos

- Script de ingesta del dataset de rostros LFW, limitado a 13 000 imágenes, que genera los CSV de personas e imágenes.
- Generador de un corpus de 25 000 expedientes en español asociados a cada persona del dataset.

### 5. Validación

Se verificó que el índice devuelve exactamente los mismos documentos y puntajes que la búsqueda sin índice. También se comprobó el costo de lecturas por término, la persistencia al reabrir el índice y la integración por SQL. Además, se confirmó que los experimentos anteriores (1 a 4) no cambian sus resultados.

### 6. Resultados experimentales

Se comparó el índice invertido con la búsqueda secuencial y con el índice GIN de PostgreSQL, sobre 20 000 expedientes.

**Construcción:**
- El tiempo y el tamaño crecen de forma lineal: con 25 000 documentos el índice se construye en unos 6 s y ocupa 7,2 MiB.
- GIN ocupa aproximadamente la mitad, porque comprime sus listas.

**Consultas:**
- Con consultas selectivas el índice lee entre 45 y 270 páginas, frente a las 6 666 de la búsqueda secuencial, y responde en milisegundos en vez de segundos.
- En el Top-10 por relevancia, el índice responde en un tiempo similar a GIN pero leyendo entre 15 y 17 veces menos bloques.
- Cuando la consulta devuelve gran parte de la tabla, la búsqueda secuencial resulta más eficiente. Esto ocurre porque el índice debe leer cada fila resultante por separado.

### 7. Principales archivos implementados

- `textual/`: índice invertido, preprocesamiento, consultas booleanas y ranking.
- `diskinverted.py` y `textsearch.py`: integración con el motor.
- `sql/`, `planner/`, `execution/` y `api/`: procesamiento de `MATCH` y `COSINE_SCORE()`.
- `scripts/ingest_lfw.py` y `scripts/generar_expedientes.py`: preparación de datos.
- `scripts/sql/textual.sql`: consultas para la demo.
- `tests/test_textual.py`: pruebas de validación.
- `benchmarks/experimento6_textual.py`: experimentos y generación de resultados.