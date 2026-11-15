"""Bloque de experimentos 7 (IVF de rostros, variando nprobe) y 8 (BoVW, variando palabras).

Cada experimento genera su CSV en benchmarks/results/, igual que los experimentos 1-4.
recall@k compara contra la búsqueda exhaustiva; no mide acierto de identidad.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

import numpy as np

from config import (ARCHIVO_EXP7, ARCHIVO_EXP8, CONSULTAS_BENCHMARK, EXP7_NPROBE, EXP8_PALABRAS,
                    K_DEFECTO, SEMILLA)
from indice import VectorIndex

# (número, modo, parámetro que se varía, valores, archivo de salida)
EXPERIMENTOS = [
    (7, "face", "nprobe", EXP7_NPROBE, ARCHIVO_EXP7),
    (8, "bovw", "query_words", EXP8_PALABRAS, ARCHIVO_EXP8),
]


# ---------------------------------------------------------------------------
# Bloque para correr un experimento y devolver sus filas
# ---------------------------------------------------------------------------
def run_experiment(idx: VectorIndex, exp: int, mode: str, param: str, values: list[int],
                   nq: int, k: int) -> list[dict]:
    random.seed(SEMILLA)
    pool = np.flatnonzero(idx.face_valid).tolist() if mode == "face" else list(range(idx.meta["count"]))
    if len(pool) < 2:
        print(f"Exp. {exp} ({mode}): se requieren al menos 2 imágenes válidas.")
        return []
    selected = random.sample(pool, min(nq, len(pool)))
    print(f"Experimento {exp} ({mode}), {len(selected)} consultas...")
    rows = []
    for docid in selected:
        # Consulta indexada: usar las mismas características para hacer justa la comparación.
        q = np.asarray(idx.faces[docid] if mode == "face" else idx.bovw_vectors[docid])
        if np.linalg.norm(q) < 1e-8:
            continue
        exact = idx.exact(q, mode, k=k, exclude_id=docid)
        wanted = set(exact["ids"])
        for setting in values:
            approx = idx.search_vector(q, mode, k=k, exclude_id=docid, **{param: setting})
            found = {r["id"] for r in approx["results"]}
            rows.append({"experimento": exp, "metodo": mode, "query_id": docid,
                         "parametro": param, "valor": setting, "k": k,
                         "recall_k": round(len(found & wanted) / max(1, len(wanted)), 5),
                         "candidatos": approx["candidates"],
                         "selectividad": approx["selectivity"],
                         "indice_ms": approx["search_ms"], "exhaustivo_ms": exact["search_ms"]})
    return rows


# ---------------------------------------------------------------------------
# Bloque para guardar el CSV e imprimir promedios
# ---------------------------------------------------------------------------
def save_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Resultados: {path}")


def print_summary(rows: list[dict], k: int) -> None:
    for setting in sorted({r["valor"] for r in rows}):
        subset = [r for r in rows if r["valor"] == setting]
        avg = lambda field: sum(r[field] for r in subset) / len(subset)
        print(f"  {subset[0]['metodo']:4} {subset[0]['parametro']}={setting:2} recall@{k}={avg('recall_k'):.3f} "
              f"candidatos={avg('candidatos'):.1f} índice={avg('indice_ms'):.3f} ms "
              f"exhaustivo={avg('exhaustivo_ms'):.3f} ms")


# ---------------------------------------------------------------------------
# Bloque principal del benchmark
# ---------------------------------------------------------------------------
def benchmark(idx: VectorIndex, out_dir: Path, nq: int = CONSULTAS_BENCHMARK, k: int = K_DEFECTO) -> None:
    produced = False
    for exp, mode, param, values, filename in EXPERIMENTOS:
        if not (idx.face if mode == "face" else idx.bovw):
            continue
        rows = run_experiment(idx, exp, mode, param, values, nq, k)
        if not rows:
            continue
        produced = True
        save_csv(rows, out_dir / filename)
        print_summary(rows, k)
    if not produced:
        print("No se produjeron experimentos: verifica índice y dataset.")
        return
    print("Nota: recall@k compara con búsqueda exhaustiva, no mide acierto de identidad.")
