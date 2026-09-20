"""Turning a record tuple into the bytes a page stores, and back.

The page layer deals in opaque byte strings; the engine deals in tuples of
Python values. This is the seam, and it is the only place that knows both.

Every record carries a leading null bitmap, one bit per column, because a fixed
width layout has no other way to tell "the integer zero" from "no value". The
bitmap rounds up to whole bytes and is followed by the fields in schema order,
packed with the format the schema derives from its declared types.
"""

from __future__ import annotations

import struct

from ..catalog import TableSchema


class RecordCodec:
    def __init__(self, schema: TableSchema):
        self._columns = schema.columns
        self._bitmap_size = (len(schema.columns) + 7) // 8
        self._body_format = schema.record_format
        self._body_size = struct.calcsize(self._body_format)

    @property
    def size(self) -> int:
        return self._bitmap_size + self._body_size

    def pack(self, record: tuple) -> bytes:
        if len(record) != len(self._columns):
            raise ValueError(
                f"el registro tiene {len(record)} campos y el esquema {len(self._columns)}"
            )
        bitmap = bytearray(self._bitmap_size)
        fields = []
        for position, (column, value) in enumerate(zip(self._columns, record, strict=False)):
            if value is None:
                bitmap[position // 8] |= 1 << (position % 8)
            fields.append(column.type.encode(value))
        return bytes(bitmap) + struct.pack(self._body_format, *fields)

    def unpack(self, raw: bytes) -> tuple:
        bitmap = raw[: self._bitmap_size]
        values = struct.unpack_from(self._body_format, raw, self._bitmap_size)
        record = []
        for position, (column, value) in enumerate(zip(self._columns, values, strict=False)):
            if bitmap[position // 8] & (1 << (position % 8)):
                record.append(None)
            else:
                record.append(column.type.decode(value))
        return tuple(record)
