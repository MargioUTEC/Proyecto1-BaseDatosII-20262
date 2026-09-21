# Resultados experimentales

Backend: `disk`

### Experimento 1 — Costo de insercion masiva

| N | estructura | segundos | disk_writes | disk_reads | filas_por_segundo |
|---|---|---|---|---|---|
| 1000 | Heap | 0.0054 | 7 | 0 | 184213 |
| 1000 | Heap + B+ | 0.0048 | 1007 | 0 | 209087 |
| 1000 | Heap + Hash | 0.0047 | 1007 | 0 | 211724 |
| 10000 | Heap | 0.0384 | 62 | 0 | 260743 |
| 10000 | Heap + B+ | 0.0462 | 10062 | 0 | 216338 |
| 10000 | Heap + Hash | 0.0484 | 10062 | 0 | 206621 |
| 50000 | Heap | 0.1891 | 307 | 0 | 264362 |
| 50000 | Heap + B+ | 0.2164 | 50307 | 0 | 231042 |
| 50000 | Heap + Hash | 0.2215 | 50307 | 0 | 225775 |
| 100000 | Heap | 0.3937 | 614 | 0 | 254008 |
| 100000 | Heap + B+ | 0.4519 | 100614 | 0 | 221271 |
| 100000 | Heap + Hash | 0.4497 | 100614 | 0 | 222394 |
| 250000 | Heap | 0.9767 | 1534 | 0 | 255952 |
| 250000 | Heap + B+ | 1.1315 | 251534 | 0 | 220954 |
| 250000 | Heap + Hash | 1.1164 | 251534 | 0 | 223943 |
| 500000 | Heap | 1.9312 | 3068 | 0 | 258909 |
| 500000 | Heap + B+ | 2.2429 | 503068 | 0 | 222928 |
| 500000 | Heap + Hash | 2.2795 | 503068 | 0 | 219348 |

### Experimento 2 — Busquedas puntuales de igualdad (N=100000, 1000 consultas)

| ruta | lecturas_media | lecturas_desv | ms_media | ms_desv |
|---|---|---|---|---|
| Full Scan (Heap) | 613.0 | 0.0 | 157.236 | 4.4184 |
| B+ Tree | 4.0 | 0.0 | 0.0549 | 0.0056 |
| Hash | 2.0 | 0.0 | 0.0581 | 0.0083 |

### Experimento 3 — Rangos con selectividad variable (N=100000)

| selectividad | filas | ruta_elegida | lecturas | ms |
|---|---|---|---|---|
| 0.1% | 100 | IndexRangeScan | 105 | 2.301 |
| 1.0% | 1000 | SeqScan | 613 | 197.834 |
| 5.0% | 5000 | SeqScan | 613 | 198.841 |
| 10.0% | 10000 | SeqScan | 613 | 202.262 |
| 25.0% | 25000 | SeqScan | 613 | 213.427 |

### Experimento 4 — Sensibilidad al tamano de bloque (N=50000)

| page_size | registros_por_pagina | paginas | lecturas_full_scan |
|---|---|---|---|
| 1024 | 40 | 1250 | 1249 |
| 2048 | 81 | 618 | 617 |
| 4096 | 163 | 307 | 306 |
| 8192 | 326 | 154 | 153 |
