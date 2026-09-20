"""Token vocabulary shared by the lexer and the parser."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class TokenType(Enum):
    KEYWORD = auto()
    IDENTIFIER = auto()
    NUMBER = auto()
    STRING = auto()
    OPERATOR = auto()
    PUNCTUATION = auto()
    EOF = auto()


KEYWORDS = frozenset(
    {
        "AND", "AS", "ASC", "BETWEEN", "BY", "CREATE", "DELETE", "DESC", "DROP",
        "EXPLAIN", "FALSE", "FROM", "INDEX", "INSERT", "INTO", "IS", "KEY",
        "LIMIT", "NOT", "NULL", "ON", "OR", "ORDER", "PRIMARY", "SELECT",
        "TABLE", "TRUE", "USING", "VALUES", "WHERE",
    }
)

# Longest first so that '>=' is matched before '>'.
OPERATORS = ("<>", "!=", ">=", "<=", "=", "<", ">")
PUNCTUATION = ("(", ")", ",", ";", "*", ".")


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: str
    line: int
    column: int

    def is_keyword(self, *names: str) -> bool:
        return self.type is TokenType.KEYWORD and self.value.upper() in {n.upper() for n in names}

    def is_punctuation(self, *symbols: str) -> bool:
        return self.type is TokenType.PUNCTUATION and self.value in symbols

    def is_operator(self, *symbols: str) -> bool:
        return self.type is TokenType.OPERATOR and self.value in symbols

    def __str__(self) -> str:
        if self.type is TokenType.EOF:
            return "fin de la sentencia"
        return f"'{self.value}'"
