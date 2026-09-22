"""Error hierarchy surfaced by the query engine.

Every error carries a message meant to be shown to the user of the SQL client,
so they are phrased in terms of the statement they typed, not internal state.
"""


class QueryEngineError(Exception):
    """Base class for anything this engine reports back to a client."""


class SQLSyntaxError(QueryEngineError):
    """The statement could not be parsed."""

    def __init__(self, message: str, line: int, column: int):
        super().__init__(f"{message} (linea {line}, columna {column})")
        self.message = message
        self.line = line
        self.column = column


class CatalogError(QueryEngineError):
    """The statement refers to a table, column or index that does not fit the catalog."""


class TypeMismatchError(QueryEngineError):
    """A literal cannot be coerced into the declared type of its column."""


class PlannerError(QueryEngineError):
    """The statement is valid but no execution plan can be built for it."""


class StorageUnavailableError(QueryEngineError):
    """The storage backend rejected an operation or is not reachable."""
