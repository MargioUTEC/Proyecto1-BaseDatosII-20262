"""Abstract syntax tree produced by the parser.

Nodes carry only what the statement said. Anything that requires knowing the
catalog -- resolving a column to its position, coercing a literal to the
declared type, choosing an access path -- happens later, in the planner.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Comparison(Enum):
    EQ = "="
    NE = "<>"
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="

    @property
    def is_equality(self) -> bool:
        return self is Comparison.EQ

    @property
    def is_range(self) -> bool:
        return self in (Comparison.LT, Comparison.LE, Comparison.GT, Comparison.GE)

    def flipped(self) -> Comparison:
        """The operator that holds when the operands swap sides."""
        return {
            Comparison.EQ: Comparison.EQ,
            Comparison.NE: Comparison.NE,
            Comparison.LT: Comparison.GT,
            Comparison.LE: Comparison.GE,
            Comparison.GT: Comparison.LT,
            Comparison.GE: Comparison.LE,
        }[self]


class LogicalOp(Enum):
    AND = "AND"
    OR = "OR"


# -- expressions --------------------------------------------------------


class Expression:
    """Marker base class for anything that evaluates to a value or a truth."""


@dataclass(frozen=True)
class ColumnRef(Expression):
    name: str


@dataclass(frozen=True)
class Literal(Expression):
    value: object


@dataclass(frozen=True)
class Compare(Expression):
    left: Expression
    operator: Comparison
    right: Expression


@dataclass(frozen=True)
class Logical(Expression):
    operator: LogicalOp
    left: Expression
    right: Expression


@dataclass(frozen=True)
class Not(Expression):
    operand: Expression


@dataclass(frozen=True)
class Between(Expression):
    operand: Expression
    lower: Expression
    upper: Expression
    negated: bool = False


@dataclass(frozen=True)
class IsNull(Expression):
    operand: Expression
    negated: bool = False


# -- statements ---------------------------------------------------------


class Statement:
    """Marker base class for a parsed statement."""


@dataclass(frozen=True)
class ColumnDefinition:
    name: str
    type_name: str
    length: int | None
    primary_key: bool = False
    nullable: bool = True


@dataclass(frozen=True)
class CreateTable(Statement):
    table: str
    columns: tuple[ColumnDefinition, ...]
    storage: str
    page_size: int | None = None


@dataclass(frozen=True)
class DropTable(Statement):
    table: str


@dataclass(frozen=True)
class CreateIndex(Statement):
    name: str
    table: str
    column: str
    kind: str


@dataclass(frozen=True)
class DropIndex(Statement):
    name: str


@dataclass(frozen=True)
class Insert(Statement):
    table: str
    columns: tuple[str, ...] | None
    rows: tuple[tuple[Expression, ...], ...]


@dataclass(frozen=True)
class OrderBy:
    column: str
    descending: bool = False


@dataclass(frozen=True)
class Select(Statement):
    table: str
    projection: tuple[str, ...] | None  # None means SELECT *
    where: Expression | None = None
    order_by: OrderBy | None = None
    limit: int | None = None


@dataclass(frozen=True)
class Delete(Statement):
    table: str
    where: Expression | None = None


@dataclass(frozen=True)
class Copy(Statement):
    """Bulk load of a delimited file straight into a table.

    Parsing one INSERT per row would spend most of the load in the lexer, so a
    dataset of hundreds of thousands of records comes in through this path and
    never becomes SQL text.
    """

    table: str
    path: str
    columns: tuple[str, ...] | None = None
    header: bool = True
    delimiter: str = ","
    null_token: str = ""


@dataclass(frozen=True)
class Explain(Statement):
    inner: Statement
