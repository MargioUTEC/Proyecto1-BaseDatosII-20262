# Puente motor -> módulo textual/ (en la raíz del repo o en QE_TEXTUAL_PATH).

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from .errors import PlannerError, StorageUnavailableError

DEFAULT_LANGUAGE = "spanish"
SCORINGS = ("TFIDF_COSINE",)


@lru_cache(maxsize=1)
def textual():
    # El paquete ``textual``, importado una sola vez.
    configured = os.getenv("QE_TEXTUAL_PATH")
    roots = [Path(configured)] if configured else []
    roots += list(Path(__file__).resolve().parents)
    root = next((str(path) for path in roots if (path / "textual" / "__init__.py").is_file()),
                None)
    if root is None:
        raise StorageUnavailableError("no se encuentra el modulo textual/ en el despliegue")
    if root not in sys.path:
        sys.path.insert(0, root)
    import textual as module

    return module


def analyzer(language: str | None = None):
    return textual().get_analyzer((language or DEFAULT_LANGUAGE).lower())


def check_query(query: str, language: str | None = None):
    # Valida la consulta de MATCH al planificar; devuelve la consulta analizada.
    module = textual()
    try:
        parsed = module.parse_query(query, analyzer(language))
    except module.QuerySyntaxError as exc:
        raise PlannerError(f"consulta MATCH invalida: {exc}") from exc
    return parsed


def row_matches(query: str, text, language: str | None = None) -> bool:
    # Evalua ``texto MATCH consulta`` sobre una sola fila (camino sin indice).
    if text is None:
        return False
    module = textual()
    current = analyzer(language)
    parsed = module.parse_query(query, current)
    terms = set(current.tokens(text))
    if parsed.boolean:
        from textual.query import matches_bag

        return matches_bag(parsed.tree, terms)
    return any(term in terms for term in parsed.positive_terms())
