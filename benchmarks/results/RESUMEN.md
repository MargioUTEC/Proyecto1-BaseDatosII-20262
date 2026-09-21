# Resultados experimentales

Backend: `disk`

### Experimento 1 — Costo de insercion masiva

| N | estructura | segundos | disk_writes | disk_reads | filas_por_segundo |
|---|---|---|---|---|---|
| 1000 | Heap | 0.005 | 7 | 0 | 200906 |
| 1000 | Heap + B+ | 0.2094 | 1023 | 5318 | 4775 |
| 1000 | Heap + Hash | 0.0051 | 1007 | 0 | 195330 |
| 10000 | Heap | 0.0398 | 62 | 0 | 251046 |
| 10000 | Heap + B+ | 2.2708 | 10237 | 59318 | 4404 |
| 10000 | Heap + Hash | 0.0506 | 10062 | 0 | 197796 |
| 50000 | Heap | 0.2027 | 307 | 0 | 246690 |
| 50000 | Heap + B+ | 13.1091 | 51190 | 299318 | 3814 |
| 50000 | Heap + Hash | 0.2329 | 50307 | 0 | 214688 |
| 100000 | Heap | 0.4042 | 614 | 0 | 247392 |
| 100000 | Heap + B+ | 30.2612 | 102384 | 625578 | 3305 |
| 100000 | Heap + Hash | 0.4868 | 100614 | 0 | 205404 |
| 250000 | Heap | 1.029 | 1534 | 0 | 242954 |
| 250000 | Heap + B+ | 87.5759 | 255959 | 1825578 | 2855 |
| 250000 | Heap + Hash | 1.232 | 251534 | 0 | 202927 |
| 500000 | Heap | 2.0476 | 3068 | 0 | 244194 |
| 500000 | Heap + B+ | 180.3072 | 511924 | 3825578 | 2773 |
| 500000 | Heap + Hash | 2.3768 | 503068 | 0 | 210364 |

### Experimento 2 — Busquedas puntuales de igualdad (N=100000, 1000 consultas)

| ruta | lecturas_media | lecturas_desv | ms_media | ms_desv |
|---|---|---|---|---|
| Full Scan (Heap) | 613.0 | 0.0 | 160.0663 | 2.7474 |
| B+ Tree | 5.0 | 0.0 | 0.1857 | 0.0082 |
| Hash | 2.0 | 0.0 | 0.0549 | 0.0037 |

### Experimento 3 — Rangos con selectividad variable (N=100000)

| selectividad | filas | ruta_elegida | lecturas | ms |
|---|---|---|---|---|
| 0.1% | 100 | IndexRangeScan | 105 | 2.718 |
| 1.0% | 1000 | SeqScan | 613 | 210.502 |
| 5.0% | 5000 | SeqScan | 613 | 206.463 |
| 10.0% | 10000 | SeqScan | 613 | 209.344 |
| 25.0% | 25000 | SeqScan | 613 | 213.562 |

### Experimento 4 — Sensibilidad al tamano de bloque (N=50000)

| page_size | registros_por_pagina | paginas | lecturas_full_scan |
|---|---|---|---|
| 1024 | 40 | 1250 | 1249 |
| 2048 | 81 | 618 | 617 |
| 4096 | 163 | 307 | 306 |
| 8192 | 326 | 154 | 153 |
