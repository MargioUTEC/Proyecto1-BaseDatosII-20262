"""The in-memory stand-ins must satisfy the same contract as the real stores."""

import pytest

from queryengine.catalog import IndexKind
from queryengine.storage.memory import MemoryIndexStore, MemoryTableStore
from queryengine.storage.port import IOCounter
from queryengine.testing.contract import (
    IndexManagerContract,
    RangeIndexContract,
    StorageEngineContract,
)


class TestMemoryTableStore(StorageEngineContract):
    @pytest.fixture
    def store(self):
        return MemoryTableStore(IOCounter())


class TestMemoryBTree(RangeIndexContract):
    kind = IndexKind.BTREE

    @pytest.fixture
    def indexes(self):
        return MemoryIndexStore(IOCounter())


class TestMemoryHash(IndexManagerContract):
    kind = IndexKind.HASH

    @pytest.fixture
    def indexes(self):
        return MemoryIndexStore(IOCounter())
