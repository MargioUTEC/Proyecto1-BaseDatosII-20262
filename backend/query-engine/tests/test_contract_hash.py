"""The on-disk extendible hash against the index contract.

It is checked with IndexManagerContract, not the range variant: hashing has no
ordered traversal, and the planner never routes a range to a hash index.
"""

import pytest

from queryengine.catalog import IndexKind
from queryengine.errors import StorageUnavailableError
from queryengine.storage.port import IOCounter
from queryengine.testing.contract import IndexManagerContract

blk01 = pytest.importorskip("queryengine.storage.blk01")
diskhash = pytest.importorskip("queryengine.storage.diskhash")

try:
    LAYER = blk01.load()
except StorageUnavailableError as exc:  # pragma: no cover - depends on checkout
    pytest.skip(f"capa fisica no disponible: {exc}", allow_module_level=True)

if LAYER.ExtendibleHash is None:  # pragma: no cover - depends on checkout
    pytest.skip("el modulo no expone ExtendibleHashFile", allow_module_level=True)


class TestDiskExtendibleHash(IndexManagerContract):
    kind = IndexKind.HASH

    @pytest.fixture
    def indexes(self, tmp_path):
        return diskhash.DiskHashIndex(IOCounter(), str(tmp_path), LAYER)

    def test_a_range_is_refused_rather_than_answered_wrong(self, built):
        indexes, meta, _ = built
        with pytest.raises(StorageUnavailableError):
            indexes.range_search(meta.name, 10, 20)
