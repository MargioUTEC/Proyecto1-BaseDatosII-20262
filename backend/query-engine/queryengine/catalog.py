"""System catalog: what tables exist, how they are laid out, what indexes cover them.

The catalog is metadata, not user data, so it is kept in a small JSON file next
to the data directory. Pages, records and indexes never go through this path --
those belong to the storage layer and are written as raw binary blocks.
"""

from __future__ import annotations

import json
import math
import os
import struct
import tempfile
from dataclasses import dataclass, field
from enum import Enum

from .errors import CatalogError
from .types import ColumnType, parse_type

DEFAULT_PAGE_SIZE = 4096
PAGE_HEADER_SIZE = 24  # page_id, record_count, free_space_offset, next, prev + padding


class StorageKind(Enum):
    HEAP = "HEAP"
    SEQUENTIAL = "SEQUENTIAL"


class IndexKind(Enum):
    BTREE = "BTREE"
    HASH = "HASH"

    @property
    def supports_range(self) -> bool:
        return self is IndexKind.BTREE


@dataclass(frozen=True)
class Column:
    name: str
    type: ColumnType
    primary_key: bool = False
    nullable: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type.kind.value,
            "length": self.type.length,
            "primary_key": self.primary_key,
            "nullable": self.nullable,
        }

    @staticmethod
    def from_dict(raw: dict) -> Column:
        return Column(
            name=raw["name"],
            type=parse_type(raw["type"], raw.get("length")),
            primary_key=raw.get("primary_key", False),
            nullable=raw.get("nullable", True),
        )


@dataclass(frozen=True)
class IndexMeta:
    name: str
    table: str
    column: str
    kind: IndexKind

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "table": self.table,
            "column": self.column,
            "kind": self.kind.value,
        }

    @staticmethod
    def from_dict(raw: dict) -> IndexMeta:
        return IndexMeta(raw["name"], raw["table"], raw["column"], IndexKind(raw["kind"]))


@dataclass
class Statistics:
    """Cardinality figures the planner uses to price an access path.

    They are maintained incrementally by the engine as rows are inserted and
    deleted. Approximate figures are fine: the planner only needs them to rank
    candidate paths against each other.
    """

    row_count: int = 0
    distinct: dict[str, int] = field(default_factory=dict)
    bounds: dict[str, list] = field(default_factory=dict)

    def distinct_for(self, column: str) -> int:
        """Estimated number of distinct values, defaulting to a tenth of the table."""
        known = self.distinct.get(column.lower())
        if known:
            return max(1, known)
        return max(1, self.row_count // 10 or 1)

    def observe(self, column: str, value) -> None:
        """Widen the known min/max of a numeric column.

        Keeping the extremes is what lets the planner price a range by the span
        it actually covers instead of falling back to a fixed guess.
        """
        if value is None or isinstance(value, bool) or not isinstance(value, int | float):
            return
        key = column.lower()
        current = self.bounds.get(key)
        if current is None:
            self.bounds[key] = [value, value]
        else:
            current[0] = min(current[0], value)
            current[1] = max(current[1], value)

    def bounds_for(self, column: str) -> tuple[float, float] | None:
        found = self.bounds.get(column.lower())
        return (found[0], found[1]) if found else None

    def to_dict(self) -> dict:
        return {
            "row_count": self.row_count,
            "distinct": dict(self.distinct),
            "bounds": {name: list(pair) for name, pair in self.bounds.items()},
        }

    @staticmethod
    def from_dict(raw: dict) -> Statistics:
        return Statistics(
            row_count=raw.get("row_count", 0),
            distinct=dict(raw.get("distinct", {})),
            bounds={name: list(pair) for name, pair in raw.get("bounds", {}).items()},
        )


@dataclass(frozen=True)
class TableSchema:
    name: str
    columns: tuple[Column, ...]
    storage: StorageKind
    page_size: int = DEFAULT_PAGE_SIZE

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def record_format(self) -> str:
        """``struct`` format of one record, derived from the declared types."""
        return "<" + "".join(column.type.struct_code for column in self.columns)

    @property
    def record_size(self) -> int:
        return struct.calcsize(self.record_format)

    @property
    def records_per_page(self) -> int:
        usable = self.page_size - PAGE_HEADER_SIZE
        return max(1, usable // (self.record_size + 1))  # +1 byte for the slot bitmap

    @property
    def primary_key(self) -> Column | None:
        return next((column for column in self.columns if column.primary_key), None)

    def column(self, name: str) -> Column:
        for candidate in self.columns:
            if candidate.name.lower() == name.lower():
                return candidate
        raise CatalogError(f"la columna '{name}' no existe en '{self.name}'")

    def has_column(self, name: str) -> bool:
        return any(candidate.name.lower() == name.lower() for candidate in self.columns)

    def index_of(self, name: str) -> int:
        for position, candidate in enumerate(self.columns):
            if candidate.name.lower() == name.lower():
                return position
        raise CatalogError(f"la columna '{name}' no existe en '{self.name}'")

    def page_count(self, row_count: int) -> int:
        return max(1, math.ceil(row_count / self.records_per_page))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "columns": [column.to_dict() for column in self.columns],
            "storage": self.storage.value,
            "page_size": self.page_size,
        }

    @staticmethod
    def from_dict(raw: dict) -> TableSchema:
        return TableSchema(
            name=raw["name"],
            columns=tuple(Column.from_dict(item) for item in raw["columns"]),
            storage=StorageKind(raw["storage"]),
            page_size=raw.get("page_size", DEFAULT_PAGE_SIZE),
        )


class Catalog:
    """In-memory catalog with write-through persistence to a JSON file."""

    def __init__(self, path: str | None = None):
        self._path = path
        self._tables: dict[str, TableSchema] = {}
        self._indexes: dict[str, IndexMeta] = {}
        self._stats: dict[str, Statistics] = {}
        if path and os.path.exists(path):
            self._load()

    # -- tables ---------------------------------------------------------

    def create_table(self, schema: TableSchema) -> None:
        key = schema.name.lower()
        if key in self._tables:
            raise CatalogError(f"la tabla '{schema.name}' ya existe")
        if not schema.columns:
            raise CatalogError(f"la tabla '{schema.name}' necesita al menos una columna")
        seen: set[str] = set()
        for column in schema.columns:
            lowered = column.name.lower()
            if lowered in seen:
                raise CatalogError(f"columna duplicada '{column.name}' en '{schema.name}'")
            seen.add(lowered)
        if schema.storage is StorageKind.SEQUENTIAL and schema.primary_key is None:
            raise CatalogError(
                f"'{schema.name}' usa SEQUENTIAL y necesita una PRIMARY KEY para ordenarse"
            )
        self._tables[key] = schema
        self._stats[key] = Statistics()
        self._flush()

    def drop_table(self, name: str) -> TableSchema:
        schema = self.table(name)
        key = schema.name.lower()
        del self._tables[key]
        self._stats.pop(key, None)
        for index in [meta for meta in self._indexes.values() if meta.table.lower() == key]:
            del self._indexes[index.name.lower()]
        self._flush()
        return schema

    def table(self, name: str) -> TableSchema:
        try:
            return self._tables[name.lower()]
        except KeyError:
            raise CatalogError(f"la tabla '{name}' no existe") from None

    def has_table(self, name: str) -> bool:
        return name.lower() in self._tables

    def tables(self) -> list[TableSchema]:
        return sorted(self._tables.values(), key=lambda schema: schema.name)

    # -- indexes --------------------------------------------------------

    def create_index(self, meta: IndexMeta) -> None:
        schema = self.table(meta.table)
        schema.column(meta.column)
        if meta.name.lower() in self._indexes:
            raise CatalogError(f"el indice '{meta.name}' ya existe")
        if self.index_for(meta.table, meta.column, meta.kind) is not None:
            raise CatalogError(
                f"ya existe un indice {meta.kind.value} sobre {meta.table}({meta.column})"
            )
        self._indexes[meta.name.lower()] = meta
        self._flush()

    def drop_index(self, name: str) -> IndexMeta:
        try:
            meta = self._indexes.pop(name.lower())
        except KeyError:
            raise CatalogError(f"el indice '{name}' no existe") from None
        self._flush()
        return meta

    def indexes_on(self, table: str, column: str | None = None) -> list[IndexMeta]:
        table_key = table.lower()
        found = [meta for meta in self._indexes.values() if meta.table.lower() == table_key]
        if column is not None:
            found = [meta for meta in found if meta.column.lower() == column.lower()]
        return sorted(found, key=lambda meta: meta.name)

    def index_for(self, table: str, column: str, kind: IndexKind) -> IndexMeta | None:
        return next((meta for meta in self.indexes_on(table, column) if meta.kind is kind), None)

    # -- statistics -----------------------------------------------------

    def statistics(self, table: str) -> Statistics:
        return self._stats.setdefault(self.table(table).name.lower(), Statistics())

    def record_insert(self, table: str, rows: int = 1) -> None:
        stats = self.statistics(table)
        stats.row_count += rows
        self._flush()

    def observe_row(self, table: str, schema: TableSchema, record: tuple) -> None:
        """Fold one freshly inserted record into the table statistics."""
        stats = self.statistics(table)
        for column, value in zip(schema.columns, record, strict=False):
            stats.observe(column.name, value)

    def record_delete(self, table: str, rows: int = 1) -> None:
        stats = self.statistics(table)
        stats.row_count = max(0, stats.row_count - rows)
        self._flush()

    def set_distinct(self, table: str, column: str, value: int) -> None:
        self.statistics(table).distinct[column.lower()] = max(1, value)
        self._flush()

    def set_bounds(self, table: str, column: str, lower, upper) -> None:
        stats = self.statistics(table)
        stats.observe(column, lower)
        stats.observe(column, upper)
        self._flush()

    # -- persistence ----------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "tables": [schema.to_dict() for schema in self._tables.values()],
            "indexes": [meta.to_dict() for meta in self._indexes.values()],
            "statistics": {name: stats.to_dict() for name, stats in self._stats.items()},
        }

    def _load(self) -> None:
        with open(self._path, encoding="utf-8") as handle:
            raw = json.load(handle)
        for item in raw.get("tables", []):
            schema = TableSchema.from_dict(item)
            self._tables[schema.name.lower()] = schema
        for item in raw.get("indexes", []):
            meta = IndexMeta.from_dict(item)
            self._indexes[meta.name.lower()] = meta
        for name, item in raw.get("statistics", {}).items():
            self._stats[name] = Statistics.from_dict(item)

    def _flush(self) -> None:
        if not self._path:
            return
        directory = os.path.dirname(os.path.abspath(self._path))
        os.makedirs(directory, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 -- closed below, then renamed
            "w", encoding="utf-8", dir=directory, delete=False, suffix=".tmp"
        )
        try:
            json.dump(self.snapshot(), handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        os.replace(handle.name, self._path)
