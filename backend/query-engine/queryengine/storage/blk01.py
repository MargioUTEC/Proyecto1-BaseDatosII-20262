"""Loading the physical storage module the storage team owns.

That module (``storage/`` at the root of the repository) uses plain top level
imports -- ``from config import PAGE_SIZE`` -- so it is loaded by putting its
directory on ``sys.path`` rather than by importing it as a package. Doing it
this way means its files are used exactly as written, with nothing to keep in
sync on either side.
"""

from __future__ import annotations

import importlib
import inspect
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
    BPlusTree: type | None = None
    variable_page_size: bool = False
    """True when Page and DiskManager accept a page_size argument.

    Detected rather than assumed, so the adapter keeps working against a
    version of the module whose block size is still a module constant -- it
    just cannot offer anything but the default size there.
    """

    def max_page_size(self) -> int:
        """Largest block the slot directory can address.

        Slot offsets are packed as unsigned shorts, so a block over 64 KiB
        would silently wrap.
        """
        return min(1 << (8 * self.slot_size // 2), 1 << 16)


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
        tree_module = _optional("bplus_tree")
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
        BPlusTree=getattr(tree_module, "BPlusTree", None) if tree_module else None,
        variable_page_size=_accepts_page_size(page_module.Page)
        and _accepts_page_size(disk_module.DiskManager),
    )


def _optional(name: str):
    """Import a module that may not exist yet, without failing the load."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _accepts_page_size(target: type) -> bool:
    try:
        return "page_size" in inspect.signature(target.__init__).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins have no signature
        return False
