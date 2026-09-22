"""The disk adapter, bound to the physical storage module, against the contract.

Skipped when that module is not on disk, so the suite still runs in isolation.
"""

import pytest

from queryengine.errors import StorageUnavailableError
from queryengine.storage.port import IOCounter
from queryengine.testing.contract import StorageEngineContract

blk01 = pytest.importorskip("queryengine.storage.blk01")
diskstore = pytest.importorskip("queryengine.storage.diskstore")

try:
    LAYER = blk01.load()
except StorageUnavailableError as exc:  # pragma: no cover - depends on checkout
    pytest.skip(f"modulo de almacenamiento fisico no disponible: {exc}", allow_module_level=True)


class TestDiskTableStore(StorageEngineContract):
    @pytest.fixture
    def store(self, tmp_path):
        return diskstore.DiskTableStore(IOCounter(), str(tmp_path), LAYER)
