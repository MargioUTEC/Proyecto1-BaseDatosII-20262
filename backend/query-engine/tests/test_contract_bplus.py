"""The on-disk B+ tree against the index contract.

Skipped when the storage module or its tree is absent.
"""

import pytest

from queryengine.catalog import IndexKind
from queryengine.errors import StorageUnavailableError
from queryengine.storage.port import IOCounter
from queryengine.testing.contract import RangeIndexContract

blk01 = pytest.importorskip("queryengine.storage.blk01")
diskindex = pytest.importorskip("queryengine.storage.diskindex")

try:
    LAYER = blk01.load()
except StorageUnavailableError as exc:  # pragma: no cover - depends on checkout
    pytest.skip(f"capa fisica no disponible: {exc}", allow_module_level=True)

if LAYER.BPlusTree is None:  # pragma: no cover - depends on checkout
    pytest.skip("el modulo de almacenamiento no expone BPlusTree", allow_module_level=True)


DUPLICATES = pytest.mark.xfail(
    reason="el arbol B+ trata las claves como unicas y descarta las repetidas",
    strict=True,
    raises=StorageUnavailableError,
)


class TestDiskBPlusTree(RangeIndexContract):
    """Cumple el contrato salvo en claves repetidas, que el arbol no soporta.

    Los dos casos quedan marcados xfail estricto en vez de eliminados: si
    alguien agrega duplicados al arbol, el fallo inesperado avisa que esto ya
    se puede quitar, y mientras tanto la limitacion sigue a la vista.
    """

    kind = IndexKind.BTREE

    @pytest.fixture
    def indexes(self, tmp_path):
        return diskindex.DiskIndexStore(IOCounter(), str(tmp_path), LAYER)

    @DUPLICATES
    def test_duplicate_keys_return_every_rid(self, indexes, meta):
        super().test_duplicate_keys_return_every_rid(indexes, meta)

    @DUPLICATES
    def test_delete_removes_only_that_pair(self, indexes, meta):
        super().test_delete_removes_only_that_pair(indexes, meta)
