"""Spatial values accepted by the R-Tree adapter."""

from __future__ import annotations

import re

_POINT = re.compile(
    r"\s*POINT\s*\(\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"\s+([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*\)\s*",
    re.IGNORECASE,
)


def parse_point(value) -> tuple[float, float]:
    """Decode the spatial column's WKT-like ``POINT(lon lat)`` value."""
    if isinstance(value, tuple) and len(value) == 2:
        return float(value[0]), float(value[1])
    if not isinstance(value, str):
        raise ValueError("el punto debe tener formato POINT(lon lat)")
    match = _POINT.fullmatch(value)
    if match is None:
        raise ValueError(f"punto invalido {value!r}; se esperaba POINT(lon lat)")
    return float(match.group(1)), float(match.group(2))
