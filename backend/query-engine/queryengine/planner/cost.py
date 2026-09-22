"""Analytical cost model, priced in block transfers.

Every access path is estimated as the number of pages it has to bring from disk.
Blocks are the unit because they are what the DiskCounter measures, so a plan's
estimate and its measured cost are directly comparable in the client.

The model deliberately charges one block per RID fetched through a secondary
index. That is the pessimistic, non-clustered assumption, and it is what makes
an index lose to a full scan once the range gets wide -- the crossover the
selectivity experiment is meant to expose.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..catalog import Statistics, TableSchema
from .predicates import KeyRange

LEAF_FANOUT = 64
DEFAULT_RANGE_SELECTIVITY = 1 / 3
HASH_PROBE_BLOCKS = 1.2  # bucket page plus an occasional overflow page


@dataclass(frozen=True)
class CostEstimate:
    blocks: float
    rows: int
    rationale: str

    def to_dict(self) -> dict:
        return {
            "estimated_blocks": round(self.blocks, 2),
            "estimated_rows": self.rows,
            "rationale": self.rationale,
        }


def selectivity(key_range: KeyRange, statistics: Statistics) -> float:
    """Fraction of the table the range is expected to keep.

    When the column's extremes are known the fraction is the share of that span
    the range covers, assuming values spread uniformly. That assumption is crude
    but it is what makes a narrow range prefer an index and a wide one prefer a
    full scan, which is the behaviour the selectivity experiment measures.
    Without extremes the model falls back to the textbook constants.
    """
    if key_range.empty:
        return 0.0
    if key_range.is_equality:
        return 1.0 / statistics.distinct_for(key_range.column)
    if not key_range.is_bounded:
        return 1.0

    span = _known_span(key_range, statistics)
    if span is not None:
        return span
    if key_range.lower is not None and key_range.upper is not None:
        return DEFAULT_RANGE_SELECTIVITY / 2
    return DEFAULT_RANGE_SELECTIVITY


def _known_span(key_range: KeyRange, statistics: Statistics) -> float | None:
    extremes = statistics.bounds_for(key_range.column)
    if extremes is None or statistics.row_count == 0:
        return None
    minimum, maximum = extremes
    width = maximum - minimum
    if width <= 0:
        return 1.0 / statistics.row_count

    lower = minimum if key_range.lower is None else max(minimum, key_range.lower)
    upper = maximum if key_range.upper is None else min(maximum, key_range.upper)
    if not isinstance(lower, int | float) or not isinstance(upper, int | float):
        return None
    if upper < lower:
        return 0.0
    fraction = (upper - lower) / width
    return min(1.0, max(1.0 / statistics.row_count, fraction))


def expected_rows(key_range: KeyRange | None, statistics: Statistics) -> int:
    if key_range is None:
        return statistics.row_count
    return max(1, round(statistics.row_count * selectivity(key_range, statistics)))


def seq_scan(schema: TableSchema, statistics: Statistics) -> CostEstimate:
    pages = schema.page_count(statistics.row_count)
    return CostEstimate(
        blocks=float(pages),
        rows=statistics.row_count,
        rationale=f"lee las {pages} paginas de la tabla",
    )


def index_equality(height: int, rows: int, hashed: bool) -> CostEstimate:
    probe = HASH_PROBE_BLOCKS if hashed else float(height)
    blocks = probe + rows
    kind = "bucket hash" if hashed else f"{height} niveles del arbol"
    return CostEstimate(
        blocks=blocks,
        rows=rows,
        rationale=f"{kind} + {rows} lecturas por RID",
    )


def index_range(height: int, rows: int) -> CostEstimate:
    leaves = math.ceil(rows / LEAF_FANOUT) if rows else 1
    blocks = height + leaves + rows
    return CostEstimate(
        blocks=float(blocks),
        rows=rows,
        rationale=f"{height} niveles + {leaves} hojas encadenadas + {rows} lecturas por RID",
    )


def binary_search(schema: TableSchema, statistics: Statistics, rows: int) -> CostEstimate:
    pages = schema.page_count(statistics.row_count)
    probes = max(1, math.ceil(math.log2(pages + 1)))
    blocks = probes + max(1, math.ceil(rows / schema.records_per_page))
    return CostEstimate(
        blocks=float(blocks),
        rows=rows,
        rationale=f"{probes} sondeos binarios sobre {pages} paginas ordenadas",
    )


def sequential_range(schema: TableSchema, statistics: Statistics, rows: int) -> CostEstimate:
    pages = schema.page_count(statistics.row_count)
    probes = max(1, math.ceil(math.log2(pages + 1)))
    swept = max(1, math.ceil(rows / schema.records_per_page))
    return CostEstimate(
        blocks=float(probes + swept),
        rows=rows,
        rationale=f"{probes} sondeos binarios + {swept} paginas contiguas",
    )
