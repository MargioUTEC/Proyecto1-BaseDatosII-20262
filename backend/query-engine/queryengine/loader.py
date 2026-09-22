"""Bulk loading of delimited files.

Going through ``INSERT`` for a real dataset would spend most of the load inside
the lexer: parsing a statement with a few thousand tuples already costs tens of
milliseconds, and at half a million rows the parser, not the disk, would be what
the insertion experiment measures. This path reads the file row by row, coerces
each field with the column's own type, and hands records straight to the storage
layer.

Statistics are folded in as rows stream past and written to the catalog once at
the end, so a load performs a single catalog flush rather than one per row.
"""

from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass, field

from .catalog import Catalog, TableSchema
from .errors import CatalogError, QueryEngineError
from .storage.port import IndexManager, IOCounter, StorageEngine

DISTINCT_TRACKING_LIMIT = 100_000
DEFAULT_MAX_REJECTS = 25


@dataclass
class LoadReport:
    table: str
    path: str
    rows_read: int = 0
    rows_inserted: int = 0
    rows_rejected: int = 0
    elapsed_ms: float = 0.0
    disk_reads: int = 0
    disk_writes: int = 0
    rejects: list[str] = field(default_factory=list)

    @property
    def rows_per_second(self) -> float:
        if self.elapsed_ms <= 0:
            return 0.0
        return self.rows_inserted / (self.elapsed_ms / 1000.0)

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "path": self.path,
            "rows_read": self.rows_read,
            "rows_inserted": self.rows_inserted,
            "rows_rejected": self.rows_rejected,
            "rows_per_second": round(self.rows_per_second, 1),
            "rejects": list(self.rejects),
            "metrics": {
                "execution_ms": round(self.elapsed_ms, 4),
                "disk_reads": self.disk_reads,
                "disk_writes": self.disk_writes,
                "disk_total": self.disk_reads + self.disk_writes,
            },
        }


class BulkLoader:
    def __init__(
        self,
        catalog: Catalog,
        storage: StorageEngine,
        indexes: IndexManager,
        io: IOCounter,
        data_dir: str | None = None,
    ):
        self._catalog = catalog
        self._storage = storage
        self._indexes = indexes
        self._io = io
        self._data_dir = os.path.abspath(data_dir) if data_dir else None

    def load(
        self,
        table: str,
        path: str,
        columns: tuple[str, ...] | None = None,
        header: bool = True,
        delimiter: str = ",",
        null_token: str = "",
        limit: int | None = None,
        max_rejects: int = DEFAULT_MAX_REJECTS,
    ) -> LoadReport:
        schema = self._catalog.table(table)
        resolved = self._resolve(path)
        report = LoadReport(table=schema.name, path=path)

        indexes = self._catalog.indexes_on(schema.name)
        index_positions = {meta.name: schema.index_of(meta.column) for meta in indexes}
        statistics = self._catalog.statistics(schema.name)
        distinct: dict[str, set] = {meta.column.lower(): set() for meta in indexes}
        overflowed: set[str] = set()

        mark = self._io.snapshot()
        started = time.perf_counter()

        with open(resolved, newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            targets = self._resolve_targets(schema, reader, columns, header)
            for line_number, raw in enumerate(reader, start=2 if header else 1):
                if limit is not None and report.rows_inserted >= limit:
                    break
                if not raw:
                    continue
                report.rows_read += 1
                try:
                    record = self._build(schema, targets, raw, null_token)
                except QueryEngineError as exc:
                    report.rows_rejected += 1
                    if len(report.rejects) < max_rejects:
                        report.rejects.append(f"linea {line_number}: {exc}")
                    elif report.rows_rejected > max_rejects * 4:
                        raise CatalogError(
                            f"{report.rows_rejected} filas rechazadas en '{path}'. "
                            f"La primera fue {report.rejects[0]}. "
                            f"Columnas del archivo emparejadas: {self._matched(schema, targets)}"
                        ) from exc
                    continue

                rid = self._storage.insert(schema.name, record)
                for meta in indexes:
                    key = record[index_positions[meta.name]]
                    self._indexes.insert(meta.name, key, rid)
                    bucket = distinct[meta.column.lower()]
                    if meta.column.lower() not in overflowed:
                        bucket.add(key)
                        if len(bucket) > DISTINCT_TRACKING_LIMIT:
                            overflowed.add(meta.column.lower())
                            bucket.clear()

                for column, value in zip(schema.columns, record, strict=False):
                    statistics.observe(column.name, value)
                report.rows_inserted += 1

        flush = getattr(self._storage, "flush", None)
        if flush is not None:
            flush(schema.name)
        report.elapsed_ms = (time.perf_counter() - started) * 1000.0
        delta = self._io.since(mark)
        report.disk_reads = delta.disk_reads
        report.disk_writes = delta.disk_writes

        for column, values in distinct.items():
            observed = report.rows_inserted if column in overflowed else len(values)
            if observed:
                self._catalog.set_distinct(schema.name, column, observed)
        self._catalog.record_insert(schema.name, report.rows_inserted)
        return report

    # -- internals ------------------------------------------------------

    @staticmethod
    def _matched(schema: TableSchema, targets: list[int | None]) -> str:
        """Which columns the header actually fed, for a diagnosable error."""
        filled = {schema.columns[position].name for position in targets if position is not None}
        missing = [column.name for column in schema.columns if column.name not in filled]
        if not missing:
            return "todas"
        return f"todas menos {', '.join(missing)}"

    def _resolve(self, path: str) -> str:
        candidate = os.path.abspath(os.path.join(self._data_dir or "", path))
        if self._data_dir and not candidate.startswith(self._data_dir + os.sep):
            raise CatalogError(f"la ruta '{path}' queda fuera del directorio de datos permitido")
        if not os.path.isfile(candidate):
            raise CatalogError(f"no se encuentra el archivo '{path}'")
        return candidate

    def _resolve_targets(
        self,
        schema: TableSchema,
        reader,
        columns: tuple[str, ...] | None,
        header: bool,
    ) -> list[int | None]:
        """For each field in the file, the record position it feeds (None to skip)."""
        if header:
            try:
                names = next(reader)
            except StopIteration:
                raise CatalogError("el archivo esta vacio") from None
            if columns is None:
                return [self._match(schema, name) for name in names]
            if len(columns) != len(names):
                return [self._position(schema, name) for name in columns]
            return [self._position(schema, name) for name in columns]
        if columns is None:
            return list(range(len(schema.columns)))
        return [self._position(schema, name) for name in columns]

    @staticmethod
    def _match(schema: TableSchema, header_name: str) -> int | None:
        """Header names are matched loosely: case, spaces and underscores ignored."""
        wanted = _normalize(header_name)
        for position, column in enumerate(schema.columns):
            if _normalize(column.name) == wanted:
                return position
        return None

    @staticmethod
    def _position(schema: TableSchema, name: str) -> int:
        return schema.index_of(name)

    @staticmethod
    def _build(
        schema: TableSchema,
        targets: list[int | None],
        raw: list[str],
        null_token: str,
    ) -> tuple:
        record: list = [None] * len(schema.columns)
        for field_index, position in enumerate(targets):
            if position is None or field_index >= len(raw):
                continue
            text = raw[field_index].strip()
            if text == null_token:
                record[position] = None
                continue
            record[position] = schema.columns[position].type.coerce(text)
        for column, value in zip(schema.columns, record, strict=False):
            if value is None and not column.nullable:
                raise CatalogError(f"la columna '{column.name}' no admite NULL")
        return tuple(record)


def _normalize(name: str) -> str:
    return name.strip().lower().replace(" ", "").replace("_", "")
