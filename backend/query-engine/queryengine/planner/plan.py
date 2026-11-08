"""Physical plan nodes.

A plan is a tree the executor walks. Each node knows the estimate the planner
gave it, so the client can put the predicted block count next to the measured
one after the query runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..catalog import IndexKind
from ..sql.ast import Expression
from .cost import CostEstimate


@dataclass
class PlanNode:
    """Base node. ``children`` keeps the tree walkable for EXPLAIN."""

    estimate: CostEstimate | None = None
    runtime: dict | None = None

    @property
    def label(self) -> str:
        return type(self).__name__

    @property
    def children(self) -> list[PlanNode]:
        return []

    def details(self) -> dict:
        return {}

    def explain(self) -> dict:
        node = {"node": self.label, **self.details()}
        if self.estimate is not None:
            node["cost"] = self.estimate.to_dict()
        if self.runtime is not None:
            node["runtime"] = self.runtime
        children = [child.explain() for child in self.children]
        if children:
            node["children"] = children
        return node

    def describe(self, depth: int = 0) -> str:
        """Flat text rendering, handy in tests and in the CLI."""
        pad = "  " * depth
        detail = " ".join(f"{key}={value}" for key, value in self.details().items())
        head = f"{pad}-> {self.label}" + (f" ({detail})" if detail else "")
        if self.estimate is not None:
            head += f"  [~{self.estimate.blocks:.1f} bloques, ~{self.estimate.rows} filas]"
        if self.runtime is not None:
            head += (f"  [idx {self.runtime['index_reads']}R/{self.runtime['index_writes']}W, "
                     f"tabla {self.runtime['table_reads']}R/{self.runtime['table_writes']}W, "
                     f"{self.runtime['index_ms']}+{self.runtime['table_ms']} ms]")
        lines = [head]
        lines.extend(child.describe(depth + 1) for child in self.children)
        return "\n".join(lines)


# -- access paths -------------------------------------------------------


@dataclass
class SeqScan(PlanNode):
    table: str = ""

    @property
    def label(self) -> str:
        return "SeqScan"

    def details(self) -> dict:
        return {"table": self.table}


@dataclass
class IndexScan(PlanNode):
    table: str = ""
    index: str = ""
    kind: IndexKind = IndexKind.BTREE
    column: str = ""
    key: object = None

    @property
    def label(self) -> str:
        return "IndexScan"

    def details(self) -> dict:
        return {
            "table": self.table,
            "index": self.index,
            "using": self.kind.value,
            "condition": f"{self.column} = {self.key!r}",
        }


@dataclass
class IndexRangeScan(PlanNode):
    table: str = ""
    index: str = ""
    column: str = ""
    lower: object = None
    upper: object = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True

    @property
    def label(self) -> str:
        return "IndexRangeScan"

    def details(self) -> dict:
        return {
            "table": self.table,
            "index": self.index,
            "using": IndexKind.BTREE.value,
            "condition": _range_text(self.column, self.lower, self.upper,
                                     self.lower_inclusive, self.upper_inclusive),
        }


@dataclass
class RTreeRadiusScan(PlanNode):
    table: str = ""
    index: str | None = None
    column: str = ""
    point: tuple[float, float] = (0.0, 0.0)
    radius: float = 0.0
    include_mbrs: bool = False

    @property
    def label(self) -> str:
        return "RTreeRadiusScan" if self.index else "SeqRadiusScan"

    def details(self) -> dict:
        return {"table": self.table, "index": self.index, "column": self.column,
                "point": self.point, "radius": self.radius}


@dataclass
class RTreeKNNScan(PlanNode):
    table: str = ""
    index: str | None = None
    column: str = ""
    point: tuple[float, float] = (0.0, 0.0)
    count: int = 1

    @property
    def label(self) -> str:
        return "RTreeKNNScan" if self.index else "SeqKNNScan"

    def details(self) -> dict:
        return {"table": self.table, "index": self.index, "column": self.column,
                "point": self.point, "k": self.count}


@dataclass
class RTreeWindowScan(PlanNode):
    table: str = ""
    index: str | None = None
    column: str = ""
    window: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    include_mbrs: bool = False

    @property
    def label(self) -> str:
        return "RTreeWindowScan" if self.index else "SeqWindowScan"

    def details(self) -> dict:
        return {"table": self.table, "index": self.index, "column": self.column,
                "window": self.window}


@dataclass
class SequentialSearch(PlanNode):
    """Binary search over the ordered area of a Sequential File."""

    table: str = ""
    column: str = ""
    key: object = None

    @property
    def label(self) -> str:
        return "SequentialSearch"

    def details(self) -> dict:
        return {"table": self.table, "condition": f"{self.column} = {self.key!r}"}


@dataclass
class SequentialRangeScan(PlanNode):
    table: str = ""
    column: str = ""
    lower: object = None
    upper: object = None

    @property
    def label(self) -> str:
        return "SequentialRangeScan"

    def details(self) -> dict:
        return {
            "table": self.table,
            "condition": _range_text(self.column, self.lower, self.upper, True, True),
        }


# -- shaping ------------------------------------------------------------


@dataclass
class Filter(PlanNode):
    child: PlanNode | None = None
    predicate: Expression | None = None
    text: str = ""

    @property
    def label(self) -> str:
        return "Filter"

    @property
    def children(self) -> list[PlanNode]:
        return [self.child] if self.child else []

    def details(self) -> dict:
        return {"condition": self.text}


@dataclass
class Project(PlanNode):
    child: PlanNode | None = None
    columns: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return "Project"

    @property
    def children(self) -> list[PlanNode]:
        return [self.child] if self.child else []

    def details(self) -> dict:
        return {"columns": ", ".join(self.columns)}


@dataclass
class Sort(PlanNode):
    child: PlanNode | None = None
    column: str = ""
    descending: bool = False
    spatial_column: str | None = None
    point: tuple[float, float] | None = None

    @property
    def label(self) -> str:
        return "Sort"

    @property
    def children(self) -> list[PlanNode]:
        return [self.child] if self.child else []

    def details(self) -> dict:
        return {"by": self.column, "order": "DESC" if self.descending else "ASC"}


@dataclass
class Limit(PlanNode):
    child: PlanNode | None = None
    count: int = 0

    @property
    def label(self) -> str:
        return "Limit"

    @property
    def children(self) -> list[PlanNode]:
        return [self.child] if self.child else []

    def details(self) -> dict:
        return {"rows": self.count}


# -- write paths --------------------------------------------------------


@dataclass
class InsertPlan(PlanNode):
    table: str = ""
    row_count: int = 0
    index_updates: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return "Insert"

    def details(self) -> dict:
        detail = {"table": self.table, "rows": self.row_count}
        if self.index_updates:
            detail["indexes"] = ", ".join(self.index_updates)
        return detail


@dataclass
class DeletePlan(PlanNode):
    table: str = ""
    child: PlanNode | None = None
    index_updates: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return "Delete"

    @property
    def children(self) -> list[PlanNode]:
        return [self.child] if self.child else []

    def details(self) -> dict:
        detail = {"table": self.table}
        if self.index_updates:
            detail["indexes"] = ", ".join(self.index_updates)
        return detail


@dataclass
class DDLPlan(PlanNode):
    """Catalog level statement: CREATE/DROP TABLE, CREATE/DROP INDEX."""

    operation: str = ""
    target: str = ""
    attributes: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.operation

    def details(self) -> dict:
        return {"target": self.target, **self.attributes}


def _range_text(column: str, lower, upper, lower_inclusive: bool, upper_inclusive: bool) -> str:
    parts = []
    if lower is not None:
        parts.append(f"{column} {'>=' if lower_inclusive else '>'} {lower!r}")
    if upper is not None:
        parts.append(f"{column} {'<=' if upper_inclusive else '<'} {upper!r}")
    return " AND ".join(parts) if parts else f"{column} sin acotar"
