"""Bloque de posting lists en disco: un .bin con todas las listas + offsets para acceso directo."""
from __future__ import annotations

from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# Bloque para guardar las listas
# ---------------------------------------------------------------------------
def save_postings(folder: Path, stem: str, groups: list[np.ndarray]) -> None:
    """Escribe {stem}_postings.bin (int32) y {stem}_offsets.npy (posición de cada lista)."""
    offsets = np.zeros(len(groups) + 1, dtype=np.int64)
    for i, group in enumerate(groups):
        offsets[i + 1] = offsets[i] + len(group)
    np.save(folder / f"{stem}_offsets.npy", offsets)
    with (folder / f"{stem}_postings.bin").open("wb") as f:
        for group in groups:
            np.asarray(group, dtype="<i4").tofile(f)


# ---------------------------------------------------------------------------
# Bloque para leer una lista por rango (sin cargar el archivo completo)
# ---------------------------------------------------------------------------
def open_postings(folder: Path, stem: str, empty: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Devuelve (offsets, datos) con memmap; si no hay datos, un arreglo vacío."""
    offsets = np.load(folder / f"{stem}_offsets.npy", mmap_mode="r")
    if empty or not int(offsets[-1]):
        return offsets, np.array([], np.int32)
    return offsets, np.memmap(folder / f"{stem}_postings.bin", dtype="<i4", mode="r")


def read_list(offsets: np.ndarray, data: np.ndarray, item: int) -> np.ndarray:
    """IDs de la lista número `item` (centroide o palabra visual)."""
    return data[int(offsets[item]):int(offsets[item + 1])]
