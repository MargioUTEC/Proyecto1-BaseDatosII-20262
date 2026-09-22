"""Column type system.

The engine is dataset agnostic: a table's binary layout is derived from the
types declared in its ``CREATE TABLE``. Each type knows how to describe itself
as a ``struct`` format code, how to coerce a parsed literal into a Python value,
and how much space one value occupies on a page.

Formats are little endian and unaligned (``<``), matching the layout the storage
layer writes to disk.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum

from .errors import CatalogError, TypeMismatchError

_TEXT_ENCODING = "utf-8"
_PAD = b"\x00"


class TypeKind(Enum):
    INT = "INT"
    BIGINT = "BIGINT"
    FLOAT = "FLOAT"
    DOUBLE = "DOUBLE"
    BOOL = "BOOL"
    CHAR = "CHAR"
    VARCHAR = "VARCHAR"
    DATE = "DATE"

    @property
    def needs_length(self) -> bool:
        return self in (TypeKind.CHAR, TypeKind.VARCHAR)


_SCALAR_CODES = {
    TypeKind.INT: "i",
    TypeKind.BIGINT: "q",
    TypeKind.FLOAT: "f",
    TypeKind.DOUBLE: "d",
    TypeKind.BOOL: "?",
}

_DATE_WIDTH = 10  # ISO-8601 'YYYY-MM-DD'


@dataclass(frozen=True)
class ColumnType:
    kind: TypeKind
    length: int | None = None

    def __post_init__(self) -> None:
        if self.kind.needs_length:
            if self.length is None or self.length <= 0:
                raise CatalogError(f"{self.kind.value} requiere una longitud positiva")
            if self.length > 65535:
                raise CatalogError(f"{self.kind.value}({self.length}) excede el maximo permitido")
        elif self.length is not None:
            raise CatalogError(f"{self.kind.value} no admite longitud")

    @property
    def struct_code(self) -> str:
        if self.kind.needs_length:
            return f"{self.length}s"
        if self.kind is TypeKind.DATE:
            return f"{_DATE_WIDTH}s"
        return _SCALAR_CODES[self.kind]

    @property
    def width(self) -> int:
        """Bytes one value of this type occupies inside a record."""
        return struct.calcsize("<" + self.struct_code)

    @property
    def is_text(self) -> bool:
        return self.kind in (TypeKind.CHAR, TypeKind.VARCHAR, TypeKind.DATE)

    @property
    def is_numeric(self) -> bool:
        return self.kind in (TypeKind.INT, TypeKind.BIGINT, TypeKind.FLOAT, TypeKind.DOUBLE)

    def coerce(self, value):
        """Convert a parsed literal into the Python value stored for this column.

        Raises TypeMismatchError when the literal cannot represent this type,
        which is what the client sees for ``WHERE salario = 'abc'``.
        """
        if value is None:
            return None
        try:
            if self.kind in (TypeKind.INT, TypeKind.BIGINT):
                if isinstance(value, float) and not value.is_integer():
                    raise ValueError("valor fraccionario")
                return int(value)
            if self.kind in (TypeKind.FLOAT, TypeKind.DOUBLE):
                return float(value)
            if self.kind is TypeKind.BOOL:
                if isinstance(value, str):
                    normalized = value.strip().lower()
                    if normalized in ("true", "t", "1"):
                        return True
                    if normalized in ("false", "f", "0"):
                        return False
                    raise ValueError("booleano no reconocido")
                return bool(value)
            text = value if isinstance(value, str) else str(value)
            if self.kind is TypeKind.DATE:
                return self._coerce_date(text)
            if len(text.encode(_TEXT_ENCODING)) > self.length:
                raise ValueError(f"excede {self.length} bytes")
            return text
        except (TypeError, ValueError) as exc:
            raise TypeMismatchError(
                f"no se puede interpretar {value!r} como {self}: {exc}"
            ) from exc

    def encode(self, value) -> bytes | int | float | bool:
        """Prepare a value for ``struct.pack``."""
        coerced = self.coerce(value)
        if not self.is_text:
            return coerced if coerced is not None else _SCALAR_DEFAULTS[self.kind]
        raw = (coerced or "").encode(_TEXT_ENCODING)
        return raw.ljust(self.width, _PAD)[: self.width]

    def decode(self, raw):
        """Turn a value read back from ``struct.unpack`` into a Python value."""
        if not self.is_text:
            return raw
        return raw.decode(_TEXT_ENCODING, "ignore").rstrip("\x00 ")

    def _coerce_date(self, text: str) -> str:
        cleaned = text.strip()
        parts = cleaned.split("-")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            raise ValueError("formato esperado YYYY-MM-DD")
        year, month, day = (int(part) for part in parts)
        if not (1 <= month <= 12 and 1 <= day <= 31):
            raise ValueError("fecha fuera de rango")
        return f"{year:04d}-{month:02d}-{day:02d}"

    def __str__(self) -> str:
        if self.kind.needs_length:
            return f"{self.kind.value}({self.length})"
        return self.kind.value


_SCALAR_DEFAULTS = {
    TypeKind.INT: 0,
    TypeKind.BIGINT: 0,
    TypeKind.FLOAT: 0.0,
    TypeKind.DOUBLE: 0.0,
    TypeKind.BOOL: False,
}


def parse_type(name: str, length: int | None = None) -> ColumnType:
    """Build a ColumnType from the identifier written in a CREATE TABLE."""
    try:
        kind = TypeKind(name.upper())
    except ValueError:
        raise CatalogError(f"tipo de dato desconocido: {name}") from None
    if kind.needs_length and length is None:
        raise CatalogError(f"{kind.value} requiere una longitud, por ejemplo {kind.value}(30)")
    return ColumnType(kind, length if kind.needs_length else None)
