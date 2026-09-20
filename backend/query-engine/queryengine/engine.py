"""The engine facade: parse, plan, execute, and report what it cost.

One statement in, one QueryResult out. The result carries the rows, the plan the
planner chose, and the exact block transfers and timings the client displays --
the whole point of the exercise being to make the cost of a query visible.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .catalog import (
    Catalog,
    Column,
    IndexKind,
    IndexMeta,
    StorageKind,
    TableSchema,
)
from .errors import CatalogError, QueryEngineError
from .execution import Executor
from .planner import Planner
from .planner.plan import DeletePlan, InsertPlan, PlanNode
from .sql import ast, parse
from .storage.port import RID, IndexManager, IOCounter, Record, StorageEngine
from .types import parse_type


@dataclass
class Metrics:
    parse_ms: float = 0.0
    plan_ms: float = 0.0
    execution_ms: float = 0.0
    disk_reads: int = 0
    disk_writes: int = 0

    @property
    def total_ms(self) -> float:
        return self.parse_ms + self.plan_ms + self.execution_ms

    def to_dict(self) -> dict:
        return {
            "parse_ms": round(self.parse_ms, 4),
            "plan_ms": round(self.plan_ms, 4),
            "execution_ms": round(self.execution_ms, 4),
            "total_ms": round(self.total_ms, 4),
            "disk_reads": self.disk_reads,
            "disk_writes": self.disk_writes,
            "disk_total": self.disk_reads + self.disk_writes,
        }


@dataclass
class QueryResult:
    statement: str
    columns: tuple[str, ...] = ()
    rows: list[tuple] = field(default_factory=list)
    affected_rows: int = 0
    message: str = ""
    plan: dict | None = None
    plan_text: str = ""
    metrics: Metrics = field(default_factory=Metrics)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict:
        return {
            "statement": self.statement,
            "columns": list(self.columns),
            "rows": [list(row) for row in self.rows],
            "row_count": self.row_count,
            "affected_rows": self.affected_rows,
            "message": self.message,
            "plan": self.plan,
            "plan_text": self.plan_text,
            "metrics": self.metrics.to_dict(),
        }


class QueryEngine:
    def __init__(
        self,
        catalog: Catalog,
        storage: StorageEngine,
        indexes: IndexManager,
        io: IOCounter | None = None,
    ):
        self._catalog = catalog
        self._storage = storage
        self._indexes = indexes
        self._io = io or storage.io
        self._planner = Planner(catalog, indexes)
        self._executor = Executor(catalog, storage, indexes)

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    # -- public API -----------------------------------------------------

    def execute(self, sql: str) -> QueryResult:
        metrics = Metrics()
        mark = self._io.snapshot()

        started = time.perf_counter()
        statement = parse(sql)
        metrics.parse_ms = _elapsed(started)

        explain_only = isinstance(statement, ast.Explain)
        if explain_only:
            statement = statement.inner

        started = time.perf_counter()
        plan = self._planner.plan(statement)
        metrics.plan_ms = _elapsed(started)

        started = time.perf_counter()
        result = self._run(statement, plan, metrics, sql, explain_only)
        metrics.execution_ms = _elapsed(started)

        delta = self._io.since(mark)
        metrics.disk_reads = delta.disk_reads
        metrics.disk_writes = delta.disk_writes
        result.metrics = metrics
        result.plan = plan.explain()
        result.plan_text = plan.describe()
        return result

    def tables(self) -> list[dict]:
        listing = []
        for schema in self._catalog.tables():
            statistics = self._catalog.statistics(schema.name)
            listing.append(
                {
                    "name": schema.name,
                    "storage": schema.storage.value,
                    "page_size": schema.page_size,
                    "record_size": schema.record_size,
                    "records_per_page": schema.records_per_page,
                    "record_format": schema.record_format,
                    "row_count": statistics.row_count,
                    "page_count": schema.page_count(statistics.row_count),
                    "columns": [
                        {
                            "name": column.name,
                            "type": str(column.type),
                            "primary_key": column.primary_key,
                            "nullable": column.nullable,
                            "bytes": column.type.width,
                        }
                        for column in schema.columns
                    ],
                    "indexes": [
                        {"name": meta.name, "column": meta.column, "kind": meta.kind.value}
                        for meta in self._catalog.indexes_on(schema.name)
                    ],
                }
            )
        return listing

    def reorganize(self, table: str) -> dict:
        schema = self._catalog.table(table)
        if schema.storage is not StorageKind.SEQUENTIAL:
            raise CatalogError(
                f"'{schema.name}' usa {schema.storage.value}; "
                "la reorganizacion solo aplica a SEQUENTIAL"
            )
        mark = self._io.snapshot()
        started = time.perf_counter()
        report = self._storage.reorganize(schema.name)
        elapsed = _elapsed(started)
        delta = self._io.since(mark)
        return {
            "table": schema.name,
            "report": report.to_dict(),
            "metrics": {
                "execution_ms": round(elapsed, 4),
                "disk_reads": delta.disk_reads,
                "disk_writes": delta.disk_writes,
                "disk_total": delta.total,
            },
        }

    # -- statement dispatch ---------------------------------------------

    def _run(
        self,
        statement: ast.Statement,
        plan: PlanNode,
        metrics: Metrics,
        sql: str,
        explain_only: bool,
    ) -> QueryResult:
        name = type(statement).__name__
        if explain_only:
            return QueryResult(statement=name, message="plan calculado sin ejecutar")
        if isinstance(statement, ast.CreateTable):
            return self._create_table(statement)
        if isinstance(statement, ast.DropTable):
            return self._drop_table(statement)
        if isinstance(statement, ast.CreateIndex):
            return self._create_index(statement)
        if isinstance(statement, ast.DropIndex):
            return self._drop_index(statement)
        if isinstance(statement, ast.Insert):
            return self._insert(statement, plan)
        if isinstance(statement, ast.Select):
            return self._select(statement, plan)
        if isinstance(statement, ast.Delete):
            return self._delete(statement, plan)
        raise QueryEngineError(f"sentencia no soportada: {name}")

    # -- DDL ------------------------------------------------------------

    def _create_table(self, statement: ast.CreateTable) -> QueryResult:
        columns = tuple(
            Column(
                name=definition.name,
                type=parse_type(definition.type_name, definition.length),
                primary_key=definition.primary_key,
                nullable=definition.nullable,
            )
            for definition in statement.columns
        )
        schema = TableSchema(
            name=statement.table,
            columns=columns,
            storage=StorageKind(statement.storage),
        )
        self._catalog.create_table(schema)
        try:
            self._storage.create_table(schema)
        except Exception:
            self._catalog.drop_table(schema.name)
            raise
        return QueryResult(
            statement="CreateTable",
            message=(
                f"tabla '{schema.name}' creada con motor {schema.storage.value}: "
                f"{schema.record_size} bytes por registro, "
                f"{schema.records_per_page} registros por pagina de {schema.page_size} B"
            ),
        )

    def _drop_table(self, statement: ast.DropTable) -> QueryResult:
        schema = self._catalog.table(statement.table)
        for meta in self._catalog.indexes_on(schema.name):
            self._indexes.drop_index(meta.name)
        self._catalog.drop_table(schema.name)
        self._storage.drop_table(schema.name)
        return QueryResult(statement="DropTable", message=f"tabla '{schema.name}' eliminada")

    def _create_index(self, statement: ast.CreateIndex) -> QueryResult:
        schema = self._catalog.table(statement.table)
        column = schema.column(statement.column)
        meta = IndexMeta(
            name=statement.name,
            table=schema.name,
            column=column.name,
            kind=IndexKind(statement.kind),
        )
        self._catalog.create_index(meta)
        try:
            self._indexes.create_index(meta, schema)
            position = schema.index_of(column.name)
            keys: set = set()
            entries = []
            for rid, record in self._storage.scan(schema.name):
                keys.add(record[position])
                entries.append((record[position], rid))
            self._indexes.bulk_load(meta.name, entries)
        except Exception:
            self._catalog.drop_index(meta.name)
            raise
        if entries:
            self._catalog.set_distinct(schema.name, column.name, len(keys))
            comparable = [key for key in keys if key is not None]
            if comparable:
                self._catalog.set_bounds(schema.name, column.name, min(comparable), max(comparable))
        return QueryResult(
            statement="CreateIndex",
            affected_rows=len(entries),
            message=(
                f"indice {meta.kind.value} '{meta.name}' creado sobre "
                f"{schema.name}({column.name}) con {len(entries)} entradas"
            ),
        )

    def _drop_index(self, statement: ast.DropIndex) -> QueryResult:
        meta = self._catalog.drop_index(statement.name)
        self._indexes.drop_index(meta.name)
        return QueryResult(statement="DropIndex", message=f"indice '{meta.name}' eliminado")

    # -- DML ------------------------------------------------------------

    def _insert(self, statement: ast.Insert, plan: PlanNode) -> QueryResult:
        schema = self._catalog.table(statement.table)
        indexes = self._catalog.indexes_on(schema.name)
        positions = {meta.name: schema.index_of(meta.column) for meta in indexes}

        inserted = 0
        for values in statement.rows:
            record = self._build_record(schema, statement.columns, values)
            rid = self._storage.insert(schema.name, record)
            for meta in indexes:
                self._indexes.insert(meta.name, record[positions[meta.name]], rid)
            self._catalog.observe_row(schema.name, schema, record)
            inserted += 1
        self._catalog.record_insert(schema.name, inserted)
        if isinstance(plan, InsertPlan):
            plan.row_count = inserted
        return QueryResult(
            statement="Insert",
            affected_rows=inserted,
            message=f"{inserted} fila(s) insertada(s) en '{schema.name}'",
        )

    def _select(self, statement: ast.Select, plan: PlanNode) -> QueryResult:
        schema = self._catalog.table(statement.table)
        columns = self._executor.output_columns(plan, schema)
        rows = [record for _, record in self._executor.execute(plan, schema)]
        return QueryResult(statement="Select", columns=columns, rows=rows)

    def _delete(self, statement: ast.Delete, plan: PlanNode) -> QueryResult:
        schema = self._catalog.table(statement.table)
        if not isinstance(plan, DeletePlan):
            raise QueryEngineError("el plan de DELETE esta mal formado")
        indexes = self._catalog.indexes_on(schema.name)
        positions = {meta.name: schema.index_of(meta.column) for meta in indexes}

        doomed: list[tuple[RID, Record]] = list(self._executor.execute(plan.child, schema))
        removed = 0
        for rid, record in doomed:
            if not self._storage.delete(schema.name, rid):
                continue
            for meta in indexes:
                self._indexes.delete(meta.name, record[positions[meta.name]], rid)
            removed += 1
        self._catalog.record_delete(schema.name, removed)
        return QueryResult(
            statement="Delete",
            affected_rows=removed,
            message=f"{removed} fila(s) eliminada(s) de '{schema.name}'",
        )

    # -- helpers --------------------------------------------------------

    def _build_record(
        self,
        schema: TableSchema,
        column_names: tuple[str, ...] | None,
        values: tuple[ast.Expression, ...],
    ) -> Record:
        if column_names is None:
            if len(values) != len(schema.columns):
                raise CatalogError(
                    f"'{schema.name}' tiene {len(schema.columns)} columnas "
                    f"y se dieron {len(values)} valores"
                )
            ordered = list(values)
        else:
            if len(column_names) != len(values):
                raise CatalogError(
                    f"se nombraron {len(column_names)} columnas y se dieron {len(values)} valores"
                )
            supplied = {}
            for name, value in zip(column_names, values, strict=False):
                supplied[schema.column(name).name.lower()] = value
            ordered = [supplied.get(column.name.lower()) for column in schema.columns]

        record = []
        for column, expression in zip(schema.columns, ordered, strict=False):
            raw = expression.value if isinstance(expression, ast.Literal) else expression
            if raw is None:
                if not column.nullable:
                    raise CatalogError(f"la columna '{column.name}' no admite NULL")
                record.append(None)
                continue
            record.append(column.type.coerce(raw))
        return tuple(record)


def _elapsed(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
