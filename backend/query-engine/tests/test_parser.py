import pytest

from queryengine.errors import SQLSyntaxError
from queryengine.sql import ast
from queryengine.sql.parser import parse


def test_create_table_reads_types_and_storage():
    statement = parse(
        "CREATE TABLE empleados (id INT PRIMARY KEY, nombre CHAR(30), salario FLOAT) "
        "USING SEQUENTIAL"
    )
    assert isinstance(statement, ast.CreateTable)
    assert statement.storage == "SEQUENTIAL"
    assert statement.columns[0].primary_key is True
    assert statement.columns[1].length == 30
    assert statement.columns[2].type_name == "FLOAT"


def test_create_table_defaults_to_heap():
    assert parse("CREATE TABLE t (id INT)").storage == "HEAP"


def test_two_primary_keys_are_rejected():
    with pytest.raises(SQLSyntaxError):
        parse("CREATE TABLE t (a INT PRIMARY KEY, b INT PRIMARY KEY)")


def test_insert_accepts_several_rows():
    statement = parse("INSERT INTO t VALUES (1, 'a'), (2, 'b')")
    assert len(statement.rows) == 2
    assert statement.rows[1][1].value == "b"


def test_insert_with_named_columns():
    statement = parse("INSERT INTO t (b, a) VALUES ('x', 1)")
    assert statement.columns == ("b", "a")


def test_select_star_has_no_projection():
    assert parse("SELECT * FROM t").projection is None


def test_select_projection_keeps_order():
    assert parse("SELECT b, a FROM t").projection == ("b", "a")


def test_and_binds_tighter_than_or():
    where = parse("SELECT * FROM t WHERE a = 1 OR b = 2 AND c = 3").where
    assert where.operator is ast.LogicalOp.OR
    assert where.right.operator is ast.LogicalOp.AND


def test_parentheses_override_precedence():
    where = parse("SELECT * FROM t WHERE (a = 1 OR b = 2) AND c = 3").where
    assert where.operator is ast.LogicalOp.AND
    assert where.left.operator is ast.LogicalOp.OR


def test_between_is_parsed():
    where = parse("SELECT * FROM t WHERE id BETWEEN 10 AND 20").where
    assert isinstance(where, ast.Between)
    assert where.negated is False


def test_not_between_is_parsed():
    assert parse("SELECT * FROM t WHERE id NOT BETWEEN 10 AND 20").where.negated is True


def test_is_not_null_is_parsed():
    where = parse("SELECT * FROM t WHERE nombre IS NOT NULL").where
    assert isinstance(where, ast.IsNull)
    assert where.negated is True


def test_order_by_and_limit():
    statement = parse("SELECT * FROM t ORDER BY salario DESC LIMIT 10")
    assert statement.order_by.descending is True
    assert statement.limit == 10


def test_create_index_defaults_to_btree():
    assert parse("CREATE INDEX i ON t(a)").kind == "BTREE"


def test_create_index_using_hash():
    assert parse("CREATE INDEX i ON t(a) USING HASH").kind == "HASH"


def test_explain_wraps_the_inner_statement():
    statement = parse("EXPLAIN SELECT * FROM t")
    assert isinstance(statement, ast.Explain)
    assert isinstance(statement.inner, ast.Select)


def test_trailing_semicolon_is_optional():
    assert parse("SELECT * FROM t;") == parse("SELECT * FROM t")


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "SELECT",
        "SELECT * FROM",
        "SELECT * FROM t WHERE",
        "SELECT * FROM t WHERE a",
        "INSERT INTO t VALUES",
        "CREATE TABLE t",
        "SELECT * FROM t LIMIT -1",
        "SELECT * FROM t; DROP TABLE t",
    ],
)
def test_malformed_statements_raise_syntax_errors(sql):
    with pytest.raises(SQLSyntaxError):
        parse(sql)


def test_syntax_error_points_at_the_token():
    with pytest.raises(SQLSyntaxError) as error:
        parse("SELECT * FROM t WHERE a == 1")
    assert error.value.line == 1
    assert error.value.column > 20
