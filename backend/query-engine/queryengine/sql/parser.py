"""Recursive descent parser for the supported SQL subset.

Grammar, informally:

    statement   := ( create_table | drop_table | create_index | drop_index
                   | insert | select | delete | explain ) [ ';' ]
    select      := SELECT projection FROM ident [ where ] [ order_by ] [ limit ]
    where       := WHERE disjunction
    disjunction := conjunction ( OR conjunction )*
    conjunction := negation ( AND negation )*
    negation    := NOT negation | predicate
    predicate   := '(' disjunction ')'
                 | operand ( comparison operand
                           | [ NOT ] BETWEEN operand AND operand
                           | IS [ NOT ] NULL )
    operand     := ident | number | string | TRUE | FALSE | NULL

Precedence is encoded in the descent: OR binds loosest, then AND, then NOT,
then the comparison operators.
"""

from __future__ import annotations

from ..errors import SQLSyntaxError
from .ast import (
    Between,
    ColumnDefinition,
    ColumnRef,
    Compare,
    Comparison,
    CreateIndex,
    CreateTable,
    Delete,
    DropIndex,
    DropTable,
    Explain,
    Expression,
    Insert,
    IsNull,
    Literal,
    Logical,
    LogicalOp,
    Not,
    OrderBy,
    Select,
    Statement,
)
from .lexer import tokenize
from .tokens import Token, TokenType

_STORAGE_KINDS = ("HEAP", "SEQUENTIAL")
_INDEX_KINDS = ("BTREE", "HASH")
_COMPARISONS = {op.value: op for op in Comparison}
_COMPARISONS["!="] = Comparison.NE


class Parser:
    def __init__(self, source: str):
        self._tokens = tokenize(source)
        self._position = 0

    def parse(self) -> Statement:
        statement = self._statement()
        self._match_punctuation(";")
        self._expect_end()
        return statement

    # -- statements -----------------------------------------------------

    def _statement(self) -> Statement:
        token = self._current()
        if token.is_keyword("EXPLAIN"):
            self._advance()
            return Explain(self._statement())
        if token.is_keyword("CREATE"):
            return self._create()
        if token.is_keyword("DROP"):
            return self._drop()
        if token.is_keyword("INSERT"):
            return self._insert()
        if token.is_keyword("SELECT"):
            return self._select()
        if token.is_keyword("DELETE"):
            return self._delete()
        raise self._error(
            "se esperaba una sentencia (SELECT, INSERT, DELETE, CREATE, DROP) "
            f"y se encontro {token}"
        )

    def _create(self) -> Statement:
        self._expect_keyword("CREATE")
        if self._match_keyword("TABLE"):
            return self._create_table()
        if self._match_keyword("INDEX"):
            return self._create_index()
        raise self._error("se esperaba TABLE o INDEX despues de CREATE")

    def _create_table(self) -> CreateTable:
        table = self._identifier("nombre de tabla")
        self._expect_punctuation("(")
        columns = [self._column_definition()]
        while self._match_punctuation(","):
            columns.append(self._column_definition())
        self._expect_punctuation(")")
        storage = "HEAP"
        if self._match_keyword("USING"):
            storage = self._one_of(_STORAGE_KINDS, "motor de almacenamiento")
        self._reject_duplicate_primary_keys(columns)
        return CreateTable(table, tuple(columns), storage)

    def _column_definition(self) -> ColumnDefinition:
        name = self._identifier("nombre de columna")
        type_name = self._identifier("tipo de dato").upper()
        length = None
        if self._match_punctuation("("):
            length = self._positive_integer("longitud del tipo")
            self._expect_punctuation(")")
        primary_key = False
        nullable = True
        while True:
            if self._match_keyword("PRIMARY"):
                self._expect_keyword("KEY")
                primary_key = True
                nullable = False
            elif self._match_keyword("NOT"):
                self._expect_keyword("NULL")
                nullable = False
            elif self._match_keyword("NULL"):
                nullable = True
            else:
                break
        return ColumnDefinition(name, type_name, length, primary_key, nullable)

    def _create_index(self) -> CreateIndex:
        name = self._identifier("nombre de indice")
        self._expect_keyword("ON")
        table = self._identifier("nombre de tabla")
        self._expect_punctuation("(")
        column = self._identifier("nombre de columna")
        self._expect_punctuation(")")
        kind = "BTREE"
        if self._match_keyword("USING"):
            kind = self._one_of(_INDEX_KINDS, "tipo de indice")
        return CreateIndex(name, table, column, kind)

    def _drop(self) -> Statement:
        self._expect_keyword("DROP")
        if self._match_keyword("TABLE"):
            return DropTable(self._identifier("nombre de tabla"))
        if self._match_keyword("INDEX"):
            return DropIndex(self._identifier("nombre de indice"))
        raise self._error("se esperaba TABLE o INDEX despues de DROP")

    def _insert(self) -> Insert:
        self._expect_keyword("INSERT")
        self._expect_keyword("INTO")
        table = self._identifier("nombre de tabla")
        columns: tuple[str, ...] | None = None
        if self._match_punctuation("("):
            names = [self._identifier("nombre de columna")]
            while self._match_punctuation(","):
                names.append(self._identifier("nombre de columna"))
            self._expect_punctuation(")")
            columns = tuple(names)
        self._expect_keyword("VALUES")
        rows = [self._value_tuple()]
        while self._match_punctuation(","):
            rows.append(self._value_tuple())
        return Insert(table, columns, tuple(rows))

    def _value_tuple(self) -> tuple[Expression, ...]:
        self._expect_punctuation("(")
        values = [self._operand()]
        while self._match_punctuation(","):
            values.append(self._operand())
        self._expect_punctuation(")")
        return tuple(values)

    def _select(self) -> Select:
        self._expect_keyword("SELECT")
        projection = self._projection()
        self._expect_keyword("FROM")
        table = self._identifier("nombre de tabla")
        where = self._where_clause()
        order_by = self._order_by_clause()
        limit = self._limit_clause()
        return Select(table, projection, where, order_by, limit)

    def _projection(self) -> tuple[str, ...] | None:
        if self._match_punctuation("*"):
            return None
        names = [self._identifier("nombre de columna")]
        while self._match_punctuation(","):
            names.append(self._identifier("nombre de columna"))
        return tuple(names)

    def _delete(self) -> Delete:
        self._expect_keyword("DELETE")
        self._expect_keyword("FROM")
        table = self._identifier("nombre de tabla")
        return Delete(table, self._where_clause())

    def _where_clause(self) -> Expression | None:
        if not self._match_keyword("WHERE"):
            return None
        return self._disjunction()

    def _order_by_clause(self) -> OrderBy | None:
        if not self._match_keyword("ORDER"):
            return None
        self._expect_keyword("BY")
        column = self._identifier("nombre de columna")
        descending = False
        if self._match_keyword("DESC"):
            descending = True
        else:
            self._match_keyword("ASC")
        return OrderBy(column, descending)

    def _limit_clause(self) -> int | None:
        if not self._match_keyword("LIMIT"):
            return None
        return self._positive_integer("valor de LIMIT")

    # -- expressions ----------------------------------------------------

    def _disjunction(self) -> Expression:
        node = self._conjunction()
        while self._match_keyword("OR"):
            node = Logical(LogicalOp.OR, node, self._conjunction())
        return node

    def _conjunction(self) -> Expression:
        node = self._negation()
        while self._match_keyword("AND"):
            node = Logical(LogicalOp.AND, node, self._negation())
        return node

    def _negation(self) -> Expression:
        if self._match_keyword("NOT"):
            return Not(self._negation())
        return self._predicate()

    def _predicate(self) -> Expression:
        if self._match_punctuation("("):
            inner = self._disjunction()
            self._expect_punctuation(")")
            return inner

        left = self._operand()

        if self._match_keyword("IS"):
            negated = bool(self._match_keyword("NOT"))
            self._expect_keyword("NULL")
            return IsNull(left, negated)

        negated_between = False
        if self._current().is_keyword("NOT") and self._peek(1).is_keyword("BETWEEN"):
            self._advance()
            negated_between = True
        if self._match_keyword("BETWEEN"):
            lower = self._operand()
            self._expect_keyword("AND")
            upper = self._operand()
            return Between(left, lower, upper, negated_between)

        token = self._current()
        if token.type is TokenType.OPERATOR:
            self._advance()
            return Compare(left, _COMPARISONS[token.value], self._operand())

        raise self._error(f"se esperaba un operador de comparacion y se encontro {token}")

    def _operand(self) -> Expression:
        token = self._current()
        if token.type is TokenType.IDENTIFIER:
            self._advance()
            return ColumnRef(token.value)
        if token.type is TokenType.NUMBER:
            self._advance()
            return Literal(self._number(token.value))
        if token.type is TokenType.STRING:
            self._advance()
            return Literal(token.value)
        if token.is_keyword("TRUE"):
            self._advance()
            return Literal(True)
        if token.is_keyword("FALSE"):
            self._advance()
            return Literal(False)
        if token.is_keyword("NULL"):
            self._advance()
            return Literal(None)
        raise self._error(f"se esperaba un valor o una columna y se encontro {token}")

    @staticmethod
    def _number(text: str):
        if any(char in text for char in ".eE"):
            return float(text)
        return int(text)

    # -- token helpers --------------------------------------------------

    def _current(self) -> Token:
        return self._tokens[self._position]

    def _peek(self, offset: int) -> Token:
        index = min(self._position + offset, len(self._tokens) - 1)
        return self._tokens[index]

    def _advance(self) -> Token:
        token = self._tokens[self._position]
        if token.type is not TokenType.EOF:
            self._position += 1
        return token

    def _match_keyword(self, name: str) -> bool:
        if self._current().is_keyword(name):
            self._advance()
            return True
        return False

    def _match_punctuation(self, symbol: str) -> bool:
        if self._current().is_punctuation(symbol):
            self._advance()
            return True
        return False

    def _expect_keyword(self, name: str) -> Token:
        if not self._current().is_keyword(name):
            raise self._error(f"se esperaba {name} y se encontro {self._current()}")
        return self._advance()

    def _expect_punctuation(self, symbol: str) -> Token:
        if not self._current().is_punctuation(symbol):
            raise self._error(f"se esperaba '{symbol}' y se encontro {self._current()}")
        return self._advance()

    def _expect_end(self) -> None:
        token = self._current()
        if token.type is not TokenType.EOF:
            raise self._error(f"texto sobrante despues del final de la sentencia: {token}")

    def _identifier(self, what: str) -> str:
        token = self._current()
        if token.type is TokenType.IDENTIFIER:
            self._advance()
            return token.value
        raise self._error(f"se esperaba un {what} y se encontro {token}")

    def _one_of(self, options: tuple[str, ...], what: str) -> str:
        token = self._current()
        text = token.value.upper()
        if token.type in (TokenType.IDENTIFIER, TokenType.KEYWORD) and text in options:
            self._advance()
            return text
        raise self._error(f"se esperaba un {what} ({' o '.join(options)}) y se encontro {token}")

    def _positive_integer(self, what: str) -> int:
        token = self._current()
        if token.type is TokenType.NUMBER and token.value.isdigit() and int(token.value) > 0:
            self._advance()
            return int(token.value)
        raise self._error(f"se esperaba un {what} entero positivo y se encontro {token}")

    def _reject_duplicate_primary_keys(self, columns: list[ColumnDefinition]) -> None:
        keys = [column.name for column in columns if column.primary_key]
        if len(keys) > 1:
            raise self._error(f"solo se admite una PRIMARY KEY, se declararon {len(keys)}")

    def _error(self, message: str) -> SQLSyntaxError:
        token = self._current()
        return SQLSyntaxError(message, token.line, token.column)


def parse(source: str) -> Statement:
    """Parse a single SQL statement."""
    if not source or not source.strip():
        raise SQLSyntaxError("la sentencia esta vacia", 1, 1)
    return Parser(source).parse()
