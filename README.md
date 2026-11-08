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