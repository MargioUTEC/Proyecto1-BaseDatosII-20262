"""Hand written scanner for the SQL subset supported by the engine.

Producing tokens eagerly keeps the parser simple and gives precise line/column
information for error messages, which is what the SQL editor in the web client
shows next to a bad statement.
"""

from __future__ import annotations

from ..errors import SQLSyntaxError
from .tokens import KEYWORDS, OPERATORS, PUNCTUATION, Token, TokenType

_IDENTIFIER_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_IDENTIFIER_BODY = _IDENTIFIER_START | set("0123456789$")


class Lexer:
    def __init__(self, source: str):
        self._source = source
        self._position = 0
        self._line = 1
        self._column = 1

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while True:
            self._skip_insignificant()
            if self._at_end():
                tokens.append(Token(TokenType.EOF, "", self._line, self._column))
                return tokens
            tokens.append(self._next_token())

    # -- scanning -------------------------------------------------------

    def _next_token(self) -> Token:
        char = self._peek()
        if char in _IDENTIFIER_START:
            return self._read_word()
        if char.isdigit() or (char == "." and self._peek(1).isdigit()):
            return self._read_number()
        if char == "'":
            return self._read_string()
        if char == '"':
            return self._read_quoted_identifier()
        for symbol in OPERATORS:
            if self._source.startswith(symbol, self._position):
                return self._consume(TokenType.OPERATOR, symbol)
        if char in PUNCTUATION:
            return self._consume(TokenType.PUNCTUATION, char)
        raise self._error(f"caracter inesperado {char!r}")

    def _read_word(self) -> Token:
        start, line, column = self._position, self._line, self._column
        while not self._at_end() and self._peek() in _IDENTIFIER_BODY:
            self._advance()
        word = self._source[start : self._position]
        kind = TokenType.KEYWORD if word.upper() in KEYWORDS else TokenType.IDENTIFIER
        return Token(kind, word, line, column)

    def _read_number(self) -> Token:
        start, line, column = self._position, self._line, self._column
        seen_dot = False
        seen_exponent = False
        while not self._at_end():
            char = self._peek()
            if char.isdigit():
                self._advance()
            elif char == "." and not seen_dot and not seen_exponent:
                seen_dot = True
                self._advance()
            elif char in "eE" and not seen_exponent and self._position > start:
                seen_exponent = True
                self._advance()
                if not self._at_end() and self._peek() in "+-":
                    self._advance()
            else:
                break
        literal = self._source[start : self._position]
        if literal[-1] in "eE+-":
            raise self._error(f"numero mal formado: {literal}")
        return Token(TokenType.NUMBER, literal, line, column)

    def _read_string(self) -> Token:
        line, column = self._line, self._column
        self._advance()  # opening quote
        chunks: list[str] = []
        while True:
            if self._at_end():
                raise SQLSyntaxError("cadena sin comilla de cierre", line, column)
            char = self._peek()
            if char == "'":
                self._advance()
                if not self._at_end() and self._peek() == "'":
                    chunks.append("'")  # doubled quote is an escaped quote
                    self._advance()
                    continue
                return Token(TokenType.STRING, "".join(chunks), line, column)
            if char == "\n":
                raise SQLSyntaxError("salto de linea dentro de una cadena", line, column)
            chunks.append(char)
            self._advance()

    def _read_quoted_identifier(self) -> Token:
        line, column = self._line, self._column
        self._advance()
        start = self._position
        while not self._at_end() and self._peek() != '"':
            if self._peek() == "\n":
                raise SQLSyntaxError("identificador sin comilla de cierre", line, column)
            self._advance()
        if self._at_end():
            raise SQLSyntaxError("identificador sin comilla de cierre", line, column)
        name = self._source[start : self._position]
        self._advance()
        if not name:
            raise SQLSyntaxError("identificador vacio", line, column)
        return Token(TokenType.IDENTIFIER, name, line, column)

    # -- helpers --------------------------------------------------------

    def _skip_insignificant(self) -> None:
        while not self._at_end():
            char = self._peek()
            if char in " \t\r\n":
                self._advance()
            elif char == "-" and self._peek(1) == "-":
                while not self._at_end() and self._peek() != "\n":
                    self._advance()
            elif char == "/" and self._peek(1) == "*":
                self._skip_block_comment()
            else:
                return

    def _skip_block_comment(self) -> None:
        line, column = self._line, self._column
        self._advance()
        self._advance()
        while not self._source.startswith("*/", self._position):
            if self._at_end():
                raise SQLSyntaxError("comentario de bloque sin cerrar", line, column)
            self._advance()
        self._advance()
        self._advance()

    def _consume(self, kind: TokenType, text: str) -> Token:
        line, column = self._line, self._column
        for _ in text:
            self._advance()
        return Token(kind, text, line, column)

    def _peek(self, offset: int = 0) -> str:
        index = self._position + offset
        return self._source[index] if index < len(self._source) else ""

    def _advance(self) -> None:
        if self._source[self._position] == "\n":
            self._line += 1
            self._column = 1
        else:
            self._column += 1
        self._position += 1

    def _at_end(self) -> bool:
        return self._position >= len(self._source)

    def _error(self, message: str) -> SQLSyntaxError:
        return SQLSyntaxError(message, self._line, self._column)


def tokenize(source: str) -> list[Token]:
    return Lexer(source).tokenize()
