# Resultados experimentales

Backend: `disk`

### Experimento 1 — Costo de insercion masiva

| N | estructura | segundos | disk_writes | disk_reads | filas_por_segundo |
|---|---|---|---|---|---|
| 1000 | Heap | 0.0104 | 27 | 0 | 95897 |
| 1000 | Sequential | 0.0189 | 63 | 27 | 52978 |
| 1000 | Heap + B+ | 0.2022 | 1043 | 5318 | 4946 |
| 1000 | Heap + Hash | 0.1161 | 2041 | 1008 | 8612 |
| 10000 | Heap | 0.0973 | 264 | 0 | 102743 |
| 10000 | Sequential | 0.1811 | 622 | 264 | 55215 |
| 10000 | Heap + B+ | 2.3062 | 10439 | 59318 | 4336 |
| 10000 | Heap + Hash | 1.0975 | 20522 | 10132 | 9112 |
| 50000 | Heap | 0.4832 | 1316 | 0 | 103467 |
| 50000 | Sequential | 0.9056 | 3102 | 1316 | 55214 |
| 50000 | Heap + B+ | 13.663 | 52199 | 299318 | 3660 |
| 50000 | Heap + Hash | 6.0682 | 102344 | 50518 | 8240 |
| 100000 | Heap | 0.9627 | 2632 | 0 | 103870 |
| 100000 | Sequential | 1.8226 | 6204 | 2632 | 54866 |
| 100000 | Heap + B+ | 30.8678 | 104402 | 625578 | 3240 |
| 100000 | Heap + Hash | 12.1914 | 204685 | 101031 | 8203 |
| 250000 | Heap | 2.391 | 6579 | 0 | 104557 |
| 250000 | Sequential | 4.5442 | 15508 | 6579 | 55015 |
| 250000 | Heap + B+ | 89.0097 | 261004 | 1825578 | 2809 |
| 250000 | Heap + Hash | 30.3932 | 511195 | 252568 | 8226 |
| 500000 | Heap | 4.775 | 13158 | 0 | 104712 |
| 500000 | Sequential | 9.0784 | 31016 | 13158 | 55076 |
| 500000 | Heap + B+ | 183.8226 | 522014 | 3825578 | 2720 |
| 500000 | Heap + Hash | 61.6204 | 1023922 | 506666 | 8114 |

### Experimento 2 — Busquedas puntuales de igualdad (N=100000, 1000 consultas)

| ruta | lecturas_media | lecturas_desv | ms_media | ms_desv |
|---|---|---|---|---|
| Full Scan (Heap) | 2631.0 | 0.0 | 251.0499 | 4.219 |
| Busqueda binaria (Sequential) | 11.85 | 1.48 | 0.7989 | 0.1043 |
| B+ Tree | 5.0 | 0.0 | 0.1836 | 0.0086 |
| Hash | 2.0 | 0.0 | 0.0944 | 0.0053 |

### Experimento 3 — Rangos con selectividad variable (N=100000)

| selectividad | filas | estructura | ruta_elegida | lecturas | ms |
|---|---|---|---|---|---|
| 0.1% | 100 | Arbol B+ | IndexRangeScan | 105 | 3.608 |
| 0.1% | 100 | Sequential | SequentialRangeScan | 17 | 1.191 |
| 0.1% | 100 | Full Scan | SeqScan | 2631 | 285.185 |
| 1.0% | 1000 | Arbol B+ | IndexRangeScan | 1010 | 21.97 |
| 1.0% | 1000 | Sequential | SequentialRangeScan | 48 | 3.487 |
| 1.0% | 1000 | Full Scan | SeqScan | 2631 | 280.398 |
| 5.0% | 5000 | Arbol B+ | SeqScan | 2631 | 289.16 |
| 5.0% | 5000 | Sequential | SequentialRangeScan | 192 | 14.157 |
| 5.0% | 5000 | Full Scan | SeqScan | 2631 | 286.354 |
| 10.0% | 10000 | Arbol B+ | SeqScan | 2631 | 291.895 |
| 10.0% | 10000 | Sequential | SequentialRangeScan | 371 | 27.318 |
| 10.0% | 10000 | Full Scan | SeqScan | 2631 | 284.597 |
| 25.0% | 25000 | Arbol B+ | SeqScan | 2631 | 290.167 |
| 25.0% | 25000 | Sequential | SequentialRangeScan | 906 | 67.593 |
| 25.0% | 25000 | Full Scan | SeqScan | 2631 | 299.867 |

### Experimento 4 — Sensibilidad al tamano de bloque (N=50000)

| page_size | registros_por_pagina | paginas | lecturas_full_scan |
|---|---|---|---|
| 1024 | 9 | 5556 | 5555 |
| 2048 | 19 | 2632 | 2631 |
| 4096 | 38 | 1316 | 1315 |
| 8192 | 77 | 650 | 649 |
