# Índice invertido en disco (páginas de 4096 B):
#   vocab.dic    pág. 0 cabecera | págs. 1..V <term 32s, df, cf, offset, nbytes> ordenadas
#                | págs. V+1.. directorio con el primer término de cada página (se carga al abrir)
#   postings.inv pág. 0 cabecera | flujo de <doc_id, tf> por término, ordenado por doc_id
#   docs.bin     pág. 0 cabecera | <rid_page, rid_slot, norma L2, longitud> por doc_id
# Construcción SPIMI: bloques en RAM -> corridas ordenadas en disco -> mezcla k-way con heap.
# w(t,d) = (1 + log10 tf) * log10(N / df); cos(q,d) = q·d / (|q| |d|); Top-K con heapq.

from __future__ import annotations

import bisect
import contextlib
import heapq
import math
import os
import shutil
import struct
import time
from array import array
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field

from .pagefile import DEFAULT_PAGE_SIZE, DiskCounter, PagedFile, StreamReader, StreamWriter
from .preprocess import MAX_TERM_BYTES, Analyzer, get_analyzer
from .query import ParsedQuery, evaluate, parse_query

VOCAB_FILE = "vocab.dic"
POSTINGS_FILE = "postings.inv"
DOCS_FILE = "docs.bin"

VERSION = 1
VOCAB_HEADER = struct.Struct("<4sHIIIIIQd16sB")
POSTINGS_HEADER = struct.Struct("<4sHIQ")
DOCS_HEADER = struct.Struct("<4sHII")
VOCAB_PAGE_HEADER = struct.Struct("<HH")         # entradas en la página, reservado
VOCAB_ENTRY = struct.Struct(f"<{MAX_TERM_BYTES}sIIQI")
FENCE_ENTRY = struct.Struct(f"<{MAX_TERM_BYTES}s")
POSTING = struct.Struct("<II")
DOC_ENTRY = struct.Struct("<iidI")
RUN_TERM = struct.Struct("<H")
RUN_COUNT = struct.Struct("<I")

DEFAULT_BLOCK_POSTINGS = 200_000


@dataclass(frozen=True)
class TermInfo:
    term: str
    df: int
    cf: int
    offset: int
    nbytes: int


@dataclass
class Hit:
    key: tuple[int, int]
    score: float
    doc_id: int


@dataclass
class SearchResult:
    hits: list[Hit]
    query: str
    parsed: str
    boolean: bool
    terms: list[str]
    candidates: int
    disk_reads: int
    elapsed_ms: float
    breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "parsed": self.parsed,
            "boolean": self.boolean,
            "terms": self.terms,
            "candidates": self.candidates,
            "disk_reads": self.disk_reads,
            "elapsed_ms": round(self.elapsed_ms, 4),
            "breakdown": dict(self.breakdown),
            "hits": [{"key": list(h.key), "score": round(h.score, 6), "doc_id": h.doc_id}
                     for h in self.hits],
        }


@dataclass
class BuildReport:
    documents: int
    terms: int
    postings: int
    spimi_blocks: int
    elapsed_ms: float
    disk_reads: int
    disk_writes: int
    bytes_on_disk: int

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _key(value) -> tuple[int, int]:
    if isinstance(value, tuple):
        return int(value[0]), int(value[1])
    return int(value), 0


def _pad(term_bytes: bytes) -> bytes:
    return term_bytes.ljust(MAX_TERM_BYTES, b"\x00")


def _unpad(raw: bytes) -> bytes:
    return raw.rstrip(b"\x00")


class InvertedIndex:
    # Índice invertido en disco abierto para consultas.

    def __init__(self, directory: str, counter=None, page_size: int = DEFAULT_PAGE_SIZE):
        self.directory = os.path.abspath(directory)
        self.counter = counter if counter is not None else DiskCounter()
        self.page_size = page_size
        self._files: list[PagedFile] = []
        self._open()

    # -- construcción -----------------------------------------------------

    @classmethod
    def build(
        cls,
        directory: str,
        documents: Iterable[tuple[object, object]],
        analyzer: Analyzer | None = None,
        counter=None,
        page_size: int = DEFAULT_PAGE_SIZE,
        block_postings: int = DEFAULT_BLOCK_POSTINGS,
    ) -> InvertedIndex:
        # Construye el índice desde cero con SPIMI.
        counter = counter if counter is not None else DiskCounter()
        analyzer = analyzer or get_analyzer()
        os.makedirs(directory, exist_ok=True)
        builder = _Builder(directory, counter, page_size, analyzer, block_postings)
        report = builder.run(documents, previous=None)
        index = cls(directory, counter, page_size)
        index.last_build = report
        return index

    def add(self, documents: Iterable[tuple[object, object]],
            block_postings: int = DEFAULT_BLOCK_POSTINGS) -> BuildReport:
        # Agrega documentos: las corridas SPIMI nuevas se mezclan con el índice vigente.
        builder = _Builder(self.directory, self.counter, self.page_size, self.analyzer,
                           block_postings)
        self.close()
        report = builder.run(documents, previous=self)
        self._open()
        self.last_build = report
        return report

    # -- apertura ---------------------------------------------------------

    def _open(self) -> None:
        self._vocab = PagedFile(os.path.join(self.directory, VOCAB_FILE), self.counter,
                                self.page_size)
        self._postings = PagedFile(os.path.join(self.directory, POSTINGS_FILE), self.counter,
                                   self.page_size)
        self._docs = PagedFile(os.path.join(self.directory, DOCS_FILE), self.counter,
                               self.page_size)
        self._files = [self._vocab, self._postings, self._docs]
        header = self._vocab.read_page(0)
        (magic, version, page_size, n_docs, n_terms, vocab_pages, fence_pages,
         total_postings, avg_len, language, stemming) = VOCAB_HEADER.unpack_from(header)
        if magic != b"VDIC":
            raise ValueError(f"{self._vocab.path} no es un vocab.dic válido")
        if page_size != self.page_size:
            raise ValueError(f"el índice usa páginas de {page_size} B, no de {self.page_size}")
        self.version = version
        self.n_docs = n_docs
        self.n_terms = n_terms
        self.vocab_pages = vocab_pages
        self.fence_pages = fence_pages
        self.total_postings = total_postings
        self.avg_doc_length = avg_len
        self.language = _unpad(language).decode("ascii") or "none"
        self.stemming = bool(stemming)
        self.analyzer = (get_analyzer(self.language, self.stemming)
                         if self.language != "none" else get_analyzer())
        self._fences: list[bytes] = []
        per_page = self.page_size // FENCE_ENTRY.size
        remaining = vocab_pages
        for page in range(fence_pages):
            raw = self._vocab.read_page(1 + vocab_pages + page)
            for slot in range(min(per_page, remaining)):
                self._fences.append(_unpad(FENCE_ENTRY.unpack_from(raw, slot * FENCE_ENTRY.size)[0]))
            remaining -= per_page
        self._docs_per_page = (self.page_size) // DOC_ENTRY.size
        self.last_build: BuildReport | None = None

    def close(self) -> None:
        for handle in self._files:
            handle.close()
        self._files = []

    def __enter__(self) -> InvertedIndex:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- acceso al diccionario y a los posteos ----------------------------

    def _vocab_page(self, page: int, cache: dict | None) -> list[tuple]:
        if cache is not None and page in cache:
            return cache[page]
        raw = self._vocab.read_page(1 + page)
        count, _ = VOCAB_PAGE_HEADER.unpack_from(raw)
        entries = [VOCAB_ENTRY.unpack_from(raw, VOCAB_PAGE_HEADER.size + i * VOCAB_ENTRY.size)
                   for i in range(count)]
        if cache is not None:
            cache[page] = entries
        return entries

    def lookup(self, term: str, cache: dict | None = None) -> TermInfo | None:
        # Ubica un término: directorio en RAM -> 1 página de vocab.dic -> búsqueda binaria.
        encoded = term.encode("utf-8")[:MAX_TERM_BYTES]
        position = bisect.bisect_right(self._fences, encoded) - 1
        if position < 0:
            return None
        entries = self._vocab_page(position, cache)
        keys = [_unpad(entry[0]) for entry in entries]
        found = bisect.bisect_left(keys, encoded)
        if found == len(keys) or keys[found] != encoded:
            return None
        _, df, cf, offset, nbytes = entries[found]
        return TermInfo(term, df, cf, offset, nbytes)

    def postings(self, term: str | TermInfo, cache: dict | None = None) -> list[tuple[int, int]]:
        # Lista de posteo ``[(doc_id, tf), ...]`` leída con seek(offset).
        info = term if isinstance(term, TermInfo) else self.lookup(term, cache)
        if info is None or info.nbytes == 0:
            return []
        reader = StreamReader(self._postings, first_page=1)
        raw = reader.read_at(info.offset, info.nbytes)
        return list(POSTING.iter_unpack(raw))

    def documents(self, doc_ids: Iterable[int]) -> dict[int, tuple[tuple[int, int], float, int]]:
        # Clave, norma y longitud de cada doc_id, leyendo cada página una sola vez.
        wanted = sorted(set(doc_ids))
        found: dict[int, tuple[tuple[int, int], float, int]] = {}
        current_page, raw = -1, b""
        for doc_id in wanted:
            page, slot = divmod(doc_id, self._docs_per_page)
            if page != current_page:
                raw = self._docs.read_page(1 + page)
                current_page = page
            rid_page, rid_slot, norm, length = DOC_ENTRY.unpack_from(raw, slot * DOC_ENTRY.size)
            found[doc_id] = ((rid_page, rid_slot), norm, length)
        return found

    def idf(self, df: int) -> float:
        return math.log10(self.n_docs / df) if df and self.n_docs else 0.0

    # -- consultas --------------------------------------------------------

    def parse(self, text: str) -> ParsedQuery:
        return parse_query(text, self.analyzer)

    def search(self, text: str, top_k: int | None = 10) -> SearchResult:
        # Booleana + ranking por similitud de coseno (CosineScore).
        started = time.perf_counter()
        before = self.counter.disk_reads
        parsed = self.parse(text)
        vocab_cache: dict = {}
        loaded: dict[str, tuple[TermInfo | None, list[tuple[int, int]]]] = {}
        reads = {"vocab": 0, "postings": 0, "docs": 0}

        def load(term: str):
            if term not in loaded:
                mark = self.counter.disk_reads
                info = self.lookup(term, vocab_cache)
                reads["vocab"] += self.counter.disk_reads - mark
                mark = self.counter.disk_reads
                loaded[term] = (info, self.postings(info) if info else [])
                reads["postings"] += self.counter.disk_reads - mark
            return loaded[term]

        allowed: set[int] | None = None
        if parsed.boolean:
            matched = evaluate(parsed.tree, lambda t: [d for d, _ in load(t)[1]],
                               lambda: list(range(self.n_docs)))
            allowed = set(matched)

        query_tf = Counter(parsed.positive_terms())
        scores: dict[int, float] = {}
        norm_q2 = 0.0
        for term, qtf in query_tf.items():
            info, plist = load(term)
            if info is None:
                continue
            idf = self.idf(info.df)
            w_q = (1.0 + math.log10(qtf)) * idf
            norm_q2 += w_q * w_q
            for doc_id, tf in plist:
                if allowed is not None and doc_id not in allowed:
                    continue
                scores[doc_id] = scores.get(doc_id, 0.0) + w_q * (1.0 + math.log10(tf)) * idf
        if allowed is not None:
            for doc_id in allowed:
                scores.setdefault(doc_id, 0.0)

        mark = self.counter.disk_reads
        docs = self.documents(scores)
        reads["docs"] = self.counter.disk_reads - mark
        norm_q = math.sqrt(norm_q2)
        ranked = []
        for doc_id, dot in scores.items():
            _, norm_d, _ = docs[doc_id]
            ranked.append((dot / (norm_d * norm_q) if dot and norm_d and norm_q else 0.0, doc_id))
        top = (heapq.nlargest(top_k, ranked, key=lambda item: (item[0], -item[1]))
               if top_k is not None else sorted(ranked, key=lambda item: (-item[0], item[1])))
        hits = [Hit(docs[doc_id][0], score, doc_id) for score, doc_id in top]
        return SearchResult(
            hits=hits,
            query=text,
            parsed=parsed.render(),
            boolean=parsed.boolean,
            terms=list(dict.fromkeys(parsed.all_terms())),
            candidates=len(scores),
            disk_reads=self.counter.disk_reads - before,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            breakdown=reads,
        )

    # -- inspección -------------------------------------------------------

    def iter_vocabulary(self) -> Iterator[TermInfo]:
        # Recorre vocab.dic en orden (lo usa la mezcla al agregar documentos).
        for page in range(self.vocab_pages):
            for term, df, cf, offset, nbytes in self._vocab_page(page, None):
                yield TermInfo(_unpad(term).decode("utf-8", "ignore"), df, cf, offset, nbytes)

    def iter_documents(self) -> Iterator[tuple[tuple[int, int], int]]:
        remaining = self.n_docs
        page = 0
        while remaining > 0:
            raw = self._docs.read_page(1 + page)
            for slot in range(min(self._docs_per_page, remaining)):
                rid_page, rid_slot, _norm, length = DOC_ENTRY.unpack_from(raw,
                                                                          slot * DOC_ENTRY.size)
                yield (rid_page, rid_slot), length
            remaining -= self._docs_per_page
            page += 1

    def files(self) -> dict[str, dict]:
        out = {}
        for handle in (self._vocab, self._postings, self._docs):
            size = os.path.getsize(handle.path)
            out[os.path.basename(handle.path)] = {"bytes": size, "pages": size // self.page_size}
        return out

    def bytes_on_disk(self) -> int:
        return sum(item["bytes"] for item in self.files().values())

    def describe(self) -> dict:
        return {
            "directory": self.directory,
            "page_size": self.page_size,
            "documents": self.n_docs,
            "terms": self.n_terms,
            "postings": self.total_postings,
            "avg_doc_length": round(self.avg_doc_length, 2),
            "vocab_pages": self.vocab_pages,
            "fence_pages": self.fence_pages,
            "entries_per_vocab_page": _vocab_capacity(self.page_size),
            "language": self.language,
            "stemming": self.stemming,
            "files": self.files(),
        }


def _vocab_capacity(page_size: int) -> int:
    return (page_size - VOCAB_PAGE_HEADER.size) // VOCAB_ENTRY.size


# -- SPIMI ------------------------------------------------------------------


class _Builder:
    # Single-Pass In-Memory Indexing con mezcla final de corridas.

    def __init__(self, directory: str, counter, page_size: int, analyzer: Analyzer,
                 block_postings: int):
        self.directory = os.path.abspath(directory)
        self.counter = counter
        self.page_size = page_size
        self.analyzer = analyzer
        self.block_postings = max(1, block_postings)

    def run(self, documents, previous: InvertedIndex | None) -> BuildReport:
        started = time.perf_counter()
        reads0, writes0 = self.counter.disk_reads, self.counter.disk_writes
        staging = os.path.join(self.directory, ".staging")
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging)
        try:
            first_doc = previous.n_docs if previous is not None else 0
            if previous is not None:
                previous._open()
            blocks, new_docs = self._invert(documents, staging, first_doc)
            runs = [self._run_reader(path) for path in blocks]
            docs: list[tuple[tuple[int, int], int]] = []
            if previous is not None:
                docs.extend(previous.iter_documents())
                runs.insert(0, _index_run(previous))
            docs.extend(new_docs)
            terms, postings = self._merge(runs, docs, staging)
            if previous is not None:
                previous.close()
            for name in (VOCAB_FILE, POSTINGS_FILE, DOCS_FILE):
                os.replace(os.path.join(staging, name), os.path.join(self.directory, name))
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        size = sum(os.path.getsize(os.path.join(self.directory, name))
                   for name in (VOCAB_FILE, POSTINGS_FILE, DOCS_FILE))
        return BuildReport(
            documents=len(docs),
            terms=terms,
            postings=postings,
            spimi_blocks=len(blocks),
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            disk_reads=self.counter.disk_reads - reads0,
            disk_writes=self.counter.disk_writes - writes0,
            bytes_on_disk=size,
        )

    # 1) invertir por bloques ------------------------------------------------

    def _invert(self, documents, staging: str, first_doc: int):
        blocks: list[str] = []
        docs: list[tuple[tuple[int, int], int]] = []
        dictionary: dict[bytes, list[tuple[int, int]]] = {}
        pending = 0
        for key, content in documents:
            if isinstance(content, Mapping):
                bag = {str(t): int(tf) for t, tf in content.items() if int(tf) > 0}
            else:
                bag = self.analyzer.bag(content)
            doc_id = first_doc + len(docs)
            docs.append((_key(key), sum(bag.values())))
            for term, tf in bag.items():
                encoded = term.encode("utf-8")[:MAX_TERM_BYTES]
                postings = dictionary.get(encoded)
                if postings is None:
                    dictionary[encoded] = [(doc_id, tf)]
                else:
                    postings.append((doc_id, tf))
            pending += len(bag)
            if pending >= self.block_postings:
                blocks.append(self._write_block(dictionary, staging, len(blocks)))
                dictionary = {}
                pending = 0
        if dictionary:
            blocks.append(self._write_block(dictionary, staging, len(blocks)))
        return blocks, docs

    def _write_block(self, dictionary, staging: str, number: int) -> str:
        # Ordena los términos del bloque y lo baja a disco como una corrida.
        path = os.path.join(staging, f"spimi_{number:04d}.blk")
        with PagedFile(path, self.counter, self.page_size, create=True) as paged:
            stream = StreamWriter(paged)
            for term in sorted(dictionary):
                postings = dictionary[term]
                stream.write(RUN_TERM.pack(len(term)) + term + RUN_COUNT.pack(len(postings)))
                stream.write(b"".join(POSTING.pack(doc, tf) for doc, tf in postings))
            stream.write(RUN_TERM.pack(0))  # fin de la corrida
            stream.close()
        return path

    def _run_reader(self, path: str) -> Iterator[tuple[bytes, list[tuple[int, int]]]]:
        with PagedFile(path, self.counter, self.page_size) as paged:
            reader = StreamReader(paged)
            while True:
                (length,) = RUN_TERM.unpack(reader.read(RUN_TERM.size))
                if length == 0:
                    return
                term = reader.read(length)
                (count,) = RUN_COUNT.unpack(reader.read(RUN_COUNT.size))
                yield term, list(POSTING.iter_unpack(reader.read(count * POSTING.size)))

    # 2) mezclar corridas y escribir los archivos definitivos --------------

    def _merge(self, runs, docs, staging: str) -> tuple[int, int]:
        n_docs = len(docs)
        norms2 = array("d", bytes(8 * n_docs))
        heap: list = []
        for number, run in enumerate(runs):
            first = next(run, None)
            if first is not None:
                heap.append((first[0], number, first[1]))
        heapq.heapify(heap)

        vocab = PagedFile(os.path.join(staging, VOCAB_FILE), self.counter, self.page_size,
                          create=True)
        postings_file = PagedFile(os.path.join(staging, POSTINGS_FILE), self.counter,
                                  self.page_size, create=True)
        stream = StreamWriter(postings_file, first_page=1)
        capacity = _vocab_capacity(self.page_size)
        page_entries: list[bytes] = []
        fences: list[bytes] = []
        vocab_pages = 0
        terms = 0
        total_postings = 0

        def flush_vocab_page() -> None:
            nonlocal vocab_pages
            raw = bytearray(self.page_size)
            VOCAB_PAGE_HEADER.pack_into(raw, 0, len(page_entries), 0)
            for i, entry in enumerate(page_entries):
                raw[VOCAB_PAGE_HEADER.size + i * VOCAB_ENTRY.size:
                    VOCAB_PAGE_HEADER.size + (i + 1) * VOCAB_ENTRY.size] = entry
            vocab.write_page(1 + vocab_pages, raw)
            vocab_pages += 1
            page_entries.clear()

        while heap:
            term, number, merged = heapq.heappop(heap)
            merged = list(merged)
            # corridas con el mismo término: el número de corrida respeta el orden de doc_id
            while heap and heap[0][0] == term:
                _, other, more = heapq.heappop(heap)
                merged.extend(more)
                self._advance(runs, other, heap)
            self._advance(runs, number, heap)

            df = len(merged)
            cf = sum(tf for _, tf in merged)
            idf = math.log10(n_docs / df) if df else 0.0
            for doc_id, tf in merged:
                weight = (1.0 + math.log10(tf)) * idf
                norms2[doc_id] += weight * weight
            offset = stream.tell()
            stream.write(b"".join(POSTING.pack(doc, tf) for doc, tf in merged))
            if not page_entries:
                fences.append(term)
            page_entries.append(VOCAB_ENTRY.pack(_pad(term), df, cf, offset, df * POSTING.size))
            terms += 1
            total_postings += df
            if len(page_entries) == capacity:
                flush_vocab_page()
        if page_entries:
            flush_vocab_page()
        stream.close()

        per_fence_page = self.page_size // FENCE_ENTRY.size
        fence_pages = math.ceil(len(fences) / per_fence_page)
        for page in range(fence_pages):
            raw = bytearray(self.page_size)
            chunk = fences[page * per_fence_page:(page + 1) * per_fence_page]
            for i, term in enumerate(chunk):
                FENCE_ENTRY.pack_into(raw, i * FENCE_ENTRY.size, _pad(term))
            vocab.write_page(1 + vocab_pages + page, raw)

        total_length = sum(length for _, length in docs)
        header = bytearray(self.page_size)
        language = self.analyzer.language if self.analyzer else "none"
        VOCAB_HEADER.pack_into(header, 0, b"VDIC", VERSION, self.page_size, n_docs, terms,
                               vocab_pages, fence_pages, total_postings,
                               total_length / n_docs if n_docs else 0.0,
                               language.encode("ascii")[:16], int(self.analyzer.stemming))
        vocab.write_page(0, header)
        header = bytearray(self.page_size)
        POSTINGS_HEADER.pack_into(header, 0, b"PINV", VERSION, self.page_size, total_postings)
        postings_file.write_page(0, header)
        vocab.close()
        postings_file.close()
        self._write_docs(staging, docs, norms2)
        return terms, total_postings

    @staticmethod
    def _advance(runs, number: int, heap: list) -> None:
        following = next(runs[number], None)
        if following is not None:
            heapq.heappush(heap, (following[0], number, following[1]))

    def _write_docs(self, staging: str, docs, norms2) -> None:
        with PagedFile(os.path.join(staging, DOCS_FILE), self.counter, self.page_size,
                       create=True) as paged:
            header = bytearray(self.page_size)
            DOCS_HEADER.pack_into(header, 0, b"DOCS", VERSION, self.page_size, len(docs))
            paged.write_page(0, header)
            per_page = self.page_size // DOC_ENTRY.size
            for page_number, start in enumerate(range(0, len(docs), per_page)):
                raw = bytearray(self.page_size)
                for slot, doc_id in enumerate(range(start, min(start + per_page, len(docs)))):
                    (rid_page, rid_slot), length = docs[doc_id]
                    DOC_ENTRY.pack_into(raw, slot * DOC_ENTRY.size, rid_page, rid_slot,
                                        math.sqrt(norms2[doc_id]), length)
                paged.write_page(1 + page_number, raw)


def _index_run(index: InvertedIndex) -> Iterator[tuple[bytes, list[tuple[int, int]]]]:
    # El índice vigente leído como una corrida SPIMI ordenada.
    for info in index.iter_vocabulary():
        yield info.term.encode("utf-8"), index.postings(info)


# -- búsqueda secuencial de referencia (Full Text Scan) ----------------------


def full_text_scan(rows: Iterable[tuple[object, str | None]], text: str,
                   analyzer: Analyzer | None = None, top_k: int | None = 10) -> list[Hit]:
    # La misma consulta sin índice: analiza cada documento al vuelo.
    from .query import matches_bag

    analyzer = analyzer or get_analyzer()
    parsed = parse_query(text, analyzer)
    bags = []
    df: Counter = Counter()
    for doc_id, (key, content) in enumerate(rows):
        bag = analyzer.bag(content) if not isinstance(content, Mapping) else Counter(content)
        bags.append((doc_id, _key(key), bag))
        df.update(bag.keys())
    n_docs = len(bags)

    def idf(term: str) -> float:
        return math.log10(n_docs / df[term]) if df.get(term) else 0.0

    query_tf = Counter(parsed.positive_terms())
    q_weights = {t: (1.0 + math.log10(c)) * idf(t) for t, c in query_tf.items()}
    norm_q = math.sqrt(sum(w * w for w in q_weights.values()))
    ranked = []
    for doc_id, key, bag in bags:
        if parsed.boolean:
            if not matches_bag(parsed.tree, set(bag)):
                continue
        elif not any(term in bag for term in query_tf):
            continue
        dot = sum(w * (1.0 + math.log10(bag[t])) * idf(t)
                  for t, w in q_weights.items() if t in bag)
        norm_d = math.sqrt(sum(((1.0 + math.log10(tf)) * idf(t)) ** 2 for t, tf in bag.items()))
        score = dot / (norm_d * norm_q) if dot and norm_d and norm_q else 0.0
        ranked.append((score, doc_id, key))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if top_k is not None:
        ranked = ranked[:top_k]
    return [Hit(key, score, doc_id) for score, doc_id, key in ranked]


def remove_index(directory: str) -> None:
    for name in (VOCAB_FILE, POSTINGS_FILE, DOCS_FILE):
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(directory, name))
    shutil.rmtree(os.path.join(directory, ".staging"), ignore_errors=True)
    with contextlib.suppress(OSError):
        os.rmdir(directory)
