# Bloque B: índice invertido en disco (SPIMI), consultas booleanas y ranking TF-IDF/coseno.

from .inverted_index import (
    BuildReport,
    Hit,
    InvertedIndex,
    SearchResult,
    full_text_scan,
    remove_index,
)
from .pagefile import DiskCounter
from .preprocess import Analyzer, get_analyzer
from .query import QuerySyntaxError, parse_query

__all__ = [
    "Analyzer",
    "BuildReport",
    "DiskCounter",
    "Hit",
    "InvertedIndex",
    "QuerySyntaxError",
    "SearchResult",
    "full_text_scan",
    "get_analyzer",
    "parse_query",
    "remove_index",
]
