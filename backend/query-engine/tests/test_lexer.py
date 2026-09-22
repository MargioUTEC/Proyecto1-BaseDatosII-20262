import pytest

from queryengine.errors import SQLSyntaxError
from queryengine.sql.lexer import tokenize
from queryengine.sql.tokens import TokenType


def kinds(sql):
    return [token.type for token in tokenize(sql)[:-1]]


def values(sql):
    return [token.value for token in tokenize(sql)[:-1]]


def test_keywords_are_case_insensitive():
    assert kinds("select") == kinds("SELECT") == [TokenType.KEYWORD]


def test_identifier_is_not_a_keyword():
    assert kinds("empleados") == [TokenType.IDENTIFIER]


def test_numbers_keep_their_literal_form():
    assert values("1 2.5 3e2 4.5e-3") == ["1", "2.5", "3e2", "4.5e-3"]


def test_operators_prefer_the_longest_match():
    assert values("a >= b") == ["a", ">=", "b"]
    assert values("a <> b") == ["a", "<>", "b"]


def test_strings_support_doubled_quotes():
    (token,) = tokenize("'O''Brien'")[:-1]
    assert token.type is TokenType.STRING
    assert token.value == "O'Brien"


def test_quoted_identifiers_keep_their_case():
    (token,) = tokenize('"Employee_ID"')[:-1]
    assert token.type is TokenType.IDENTIFIER
    assert token.value == "Employee_ID"


def test_line_comments_are_skipped():
    assert values("SELECT -- nota\n *") == ["SELECT", "*"]


def test_block_comments_are_skipped():
    assert values("SELECT /* nota */ *") == ["SELECT", "*"]


def test_positions_track_newlines():
    token = tokenize("SELECT\n  *")[1]
    assert (token.line, token.column) == (2, 3)


@pytest.mark.parametrize(
    "sql, message",
    [
        ("'sin cerrar", "comilla"),
        ("/* sin cerrar", "comentario"),
        ("SELECT # FROM t", "caracter inesperado"),
    ],
)
def test_lexer_reports_where_it_failed(sql, message):
    with pytest.raises(SQLSyntaxError) as error:
        tokenize(sql)
    assert message in str(error.value)
