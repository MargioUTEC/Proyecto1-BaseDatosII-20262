"""Query engine: SQL parser, planner and executor for the CS2042 storage engine."""

from .catalog import Catalog, IndexKind, StorageKind, TableSchema
from .engine import Metrics, QueryEngine, QueryResult
from .errors import (
    CatalogError,
    PlannerError,
    QueryEngineError,
    SQLSyntaxError,
    StorageUnavailableError,
    TypeMismatchError,
)

__all__ = [
    "Catalog",
    "CatalogError",
    "IndexKind",
    "Metrics",
    "PlannerError",
    "QueryEngine",
    "QueryEngineError",
    "QueryResult",
    "SQLSyntaxError",
    "StorageKind",
    "StorageUnavailableError",
    "TableSchema",
    "TypeMismatchError",
]

__version__ = "0.1.0"
