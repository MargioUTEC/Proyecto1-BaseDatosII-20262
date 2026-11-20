# Resultados experimentales

Backend: `memory`

### Experimento 1 — Costo de insercion masiva

| N | estructura | segundos | disk_writes | disk_reads | filas_por_segundo |
|---|---|---|---|---|---|
| 1000 | Heap | 0.0571 | 1000 | 1000 | 17517 |
| 1000 | Sequential | 0.0921 | 1036 | 4035 | 10862 |
| 1000 | Heap + B+ | 0.0915 | 2000 | 1000 | 10925 |
| 1000 | Heap + Hash | 0.1027 | 2000 | 1000 | 9734 |
| 5000 | Heap | 0.1012 | 5000 | 5000 | 49393 |
| 5000 | Sequential | 0.2212 | 5179 | 30739 | 22602 |
| 5000 | Heap + B+ | 0.2279 | 10000 | 5000 | 21943 |
| 5000 | Heap + Hash | 0.2291 | 10000 | 5000 | 21828 |
| 20000 | Heap | 0.3132 | 20000 | 20000 | 63850 |
| 20000 | Sequential | 0.4146 | 20715 | 162024 | 48238 |
| 20000 | Heap + B+ | 0.4089 | 40000 | 20000 | 48910 |
| 20000 | Heap + Hash | 0.3987 | 40000 | 20000 | 50162 |

### Experimento 2 — Busquedas puntuales de igualdad (N=20000, 100 consultas)

| ruta | lecturas_media | lecturas_desv | ms_media | ms_desv |
|---|---|---|---|---|
| Full Scan (Heap) | 527.0 | 0.0 | 68.438 | 59.9902 |
| Busqueda binaria (Sequential) | 10.0 | 0.0 | 0.1737 | 0.0674 |
| B+ Tree | 4.0 | 0.0 | 0.1672 | 0.0571 |
| Hash | 2.0 | 0.0 | 0.1423 | 0.0547 |

### Experimento 3 — Rangos con selectividad variable (N=20000)

| selectividad | filas | estructura | ruta_elegida | lecturas | ms |
|---|---|---|---|---|---|
| 0.1% | 20 | Arbol B+ | IndexRangeScan | 24 | 0.603 |
| 0.1% | 20 | Sequential | SequentialRangeScan | 11 | 0.637 |
| 0.1% | 20 | Full Scan | SeqScan | 527 | 104.841 |
| 1.0% | 200 | Arbol B+ | IndexRangeScan | 207 | 0.471 |
| 1.0% | 200 | Sequential | SequentialRangeScan | 16 | 0.674 |
| 1.0% | 200 | Full Scan | SeqScan | 527 | 81.75 |
| 5.0% | 1000 | Arbol B+ | SeqScan | 527 | 72.779 |
| 5.0% | 1000 | Sequential | SequentialRangeScan | 37 | 2.842 |
| 5.0% | 1000 | Full Scan | SeqScan | 527 | 89.053 |
| 10.0% | 2000 | Arbol B+ | SeqScan | 527 | 72.97 |
| 10.0% | 2000 | Sequential | SequentialRangeScan | 63 | 4.939 |
| 10.0% | 2000 | Full Scan | SeqScan | 527 | 74.029 |
| 25.0% | 5000 | Arbol B+ | SeqScan | 527 | 75.419 |
| 25.0% | 5000 | Sequential | SequentialRangeScan | 142 | 13.244 |
| 25.0% | 5000 | Full Scan | SeqScan | 527 | 76.647 |

### Experimento 4 — Sensibilidad al tamano de bloque (N=20000)

| page_size | registros_por_pagina | paginas | lecturas_full_scan |
|---|---|---|---|
| 1024 | 9 | 2223 | 2223 |
| 2048 | 19 | 1053 | 1053 |
| 4096 | 38 | 527 | 527 |
| 8192 | 77 | 260 | 260 |
