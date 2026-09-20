"""Loading the physical storage module the storage team owns.

That module (``storage/`` at the root of the repository) uses plain top level
imports -- ``from config import PAGE_SIZE`` -- so it is loaded by putting its
directory on ``sys.path`` rather than by importing it as a package. Doing it
this way means its files are used exactly as written, with nothing to keep in
sync on either side.
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass

from ..errors import StorageUnavailableError

DEFAULT_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "storage")
)


@dataclass(frozen=True)
class PhysicalLayer:
    """The pieces of the storage module the adapter needs."""

    Page: type
    DiskManager: type
    page_size: int
    header_size: int
    slot_size: int
    slot_format: str
    source: str


def load(path: str | None = None) -> PhysicalLayer:
    directory = os.path.abspath(path or os.getenv("QE_BLK01_PATH") or DEFAULT_PATH)
    if not os.path.isdir(directory):
        raise StorageUnavailableError(
            f"no se encuentra el modulo de almacenamiento fisico en '{directory}'. "
            "Indica su ruta con QE_BLK01_PATH."
        )
    if directory not in sys.path:
        sys.path.insert(0, directory)
    try:
        config = importlib.import_module("config")
        page_module = importlib.import_module("page")
        disk_module = importlib.import_module("disk_management")
    except ImportError as exc:
        raise StorageUnavailableError(
            f"el modulo de almacenamiento en '{directory}' no se pudo importar: {exc}"
        ) from exc

    missing = [
        name
        for name, owner in (("Page", page_module), ("DiskManager", disk_module))
        if not hasattr(owner, name)
    ]
    if missing:
        raise StorageUnavailableError(
            f"el modulo de almacenamiento no expone {', '.join(missing)}"
        )

    return PhysicalLayer(
        Page=page_module.Page,
        DiskManager=disk_module.DiskManager,
        page_size=config.PAGE_SIZE,
        header_size=page_module.PAGE_HEADER_SIZE,
        slot_size=page_module.SLOT_SIZE,
        slot_format=page_module.SLOT_FORMAT,
        source=directory,
    )
