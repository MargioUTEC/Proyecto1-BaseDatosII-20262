# Pipeline léxico: minúsculas -> tokens -> sin tildes -> stopwords -> stemming Snowball (nltk).
# Las tildes se quitan antes del stemmer para que 'fotografia' y 'fotografía' den la misma raíz.
# Documentos y consultas pasan por el mismo Analyzer.

from __future__ import annotations

import os
import re
import unicodedata
import warnings
from collections import Counter
from functools import lru_cache

MAX_TERM_BYTES = 32
_HERE = os.path.dirname(os.path.abspath(__file__))
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_LANGUAGES = {"spanish": "stoplist_es.txt", "english": "stoplist_en.txt"}


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def load_stoplist(language: str) -> frozenset[str]:
    try:
        filename = _LANGUAGES[language]
    except KeyError:
        raise ValueError(f"idioma no soportado: {language!r} (spanish | english)") from None
    with open(os.path.join(_HERE, filename), encoding="utf-8") as handle:
        return frozenset(strip_accents(line.strip().lower()) for line in handle if line.strip())


def _snowball(language: str):
    try:
        from nltk.stem.snowball import SnowballStemmer
    except ImportError:  # pragma: no cover - nltk es dependencia declarada
        warnings.warn("nltk no está instalado: se indexa sin stemming", stacklevel=3)
        return lambda word: word
    return SnowballStemmer(language).stem


class Analyzer:
    # Convierte texto en la lista de términos normalizados que se indexan.

    def __init__(self, language: str = "spanish", stemming: bool = True,
                 min_length: int = 2, keep_numbers: bool = False):
        self.language = language
        self.stemming = stemming
        self.min_length = min_length
        self.keep_numbers = keep_numbers
        self.stopwords = load_stoplist(language)
        stem = _snowball(language) if stemming else (lambda word: word)
        self._stem = lru_cache(maxsize=200_000)(stem)

    def describe(self) -> dict:
        return {
            "language": self.language,
            "stemming": "snowball" if self.stemming else "none",
            "stopwords": len(self.stopwords),
        }

    def tokens(self, text: str | None) -> list[str]:
        # Términos en el orden en que aparecen (con repeticiones).
        if not text:
            return []
        terms = []
        for raw in _TOKEN.findall(text.casefold()):
            if raw.isdigit() and not self.keep_numbers:
                continue
            plain = strip_accents(raw)
            if len(plain) < self.min_length or plain in self.stopwords:
                continue
            terms.append(_fit(self._stem(plain)))
        return terms

    def bag(self, text: str | None) -> Counter:
        # Frecuencia de cada término en el texto: tf_{t,d}.
        return Counter(self.tokens(text))

    def trace(self, text: str) -> list[dict]:
        # Cada palabra y lo que el pipeline hizo con ella (para la demo).
        steps = []
        for raw in _TOKEN.findall(text.casefold()):
            plain = strip_accents(raw)
            if raw.isdigit() and not self.keep_numbers:
                steps.append({"token": raw, "result": None, "reason": "número"})
            elif len(plain) < self.min_length:
                steps.append({"token": raw, "result": None, "reason": "muy corto"})
            elif plain in self.stopwords:
                steps.append({"token": raw, "result": None, "reason": "stopword"})
            else:
                steps.append({"token": raw, "result": _fit(self._stem(plain)),
                              "reason": "raíz"})
        return steps


def _fit(term: str) -> str:
    # Trunca a MAX_TERM_BYTES en UTF-8 sin partir un carácter.
    encoded = term.encode("utf-8")
    if len(encoded) <= MAX_TERM_BYTES:
        return term
    return encoded[:MAX_TERM_BYTES].decode("utf-8", "ignore")


@lru_cache(maxsize=8)
def get_analyzer(language: str = "spanish", stemming: bool = True) -> Analyzer:
    return Analyzer(language, stemming)
