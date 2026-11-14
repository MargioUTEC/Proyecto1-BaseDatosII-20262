# CLI: python -m textual build <dir> <csv> | search <dir> "consulta" | inspect <dir> | analyze "texto"
# inspect vuelca las páginas binarias de vocab.dic / postings.inv / docs.bin (para el video).

from __future__ import annotations

import argparse
import csv
import os
import sys

from .inverted_index import (
    DOC_ENTRY,
    DOCS_FILE,
    POSTINGS_FILE,
    VOCAB_ENTRY,
    VOCAB_FILE,
    VOCAB_PAGE_HEADER,
    InvertedIndex,
    _unpad,
)
from .pagefile import DiskCounter
from .preprocess import get_analyzer


def _build(args) -> None:
    csv.field_size_limit(10_000_000)
    with open(args.csv, newline="", encoding="utf-8") as fh:
        rows = [(int(row[args.id]), row[args.columna]) for row in csv.DictReader(fh)]
    counter = DiskCounter()
    index = InvertedIndex.build(args.dir, rows, get_analyzer(args.idioma), counter)
    report = index.last_build
    print(f"{report.documents} documentos, {report.terms} términos, {report.postings} posteos, "
          f"{report.spimi_blocks} bloques SPIMI, {report.elapsed_ms / 1000:.2f} s, "
          f"{report.disk_writes} escrituras")
    for name, item in index.files().items():
        print(f"  {name:13} {item['pages']:6} páginas  {item['bytes']:>12,} B")
    index.close()


def _search(args) -> None:
    with InvertedIndex(args.dir) as index:
        result = index.search(args.consulta, args.k)
        print(f"consulta analizada: {result.parsed}  ({'booleana' if result.boolean else 'texto libre'})")
        print(f"candidatos: {result.candidates}  lecturas: {result.disk_reads} {result.breakdown}  "
              f"{result.elapsed_ms:.2f} ms")
        for rank, hit in enumerate(result.hits, start=1):
            print(f"  {rank:3}. clave={hit.key}  coseno={hit.score:.4f}")


def _hex(raw: bytes, width: int = 16, lines: int = 4) -> str:
    out = []
    for i in range(0, min(len(raw), width * lines), width):
        chunk = raw[i:i + width]
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"    {i:04x}  {chunk.hex(' '):47}  {text}")
    return "\n".join(out)


def _inspect(args) -> None:
    with InvertedIndex(args.dir) as index:
        info = index.describe()
        for key in ("documents", "terms", "postings", "vocab_pages", "fence_pages",
                    "entries_per_vocab_page", "language", "stemming"):
            print(f"{key:24} {info[key]}")
        for name, item in info["files"].items():
            print(f"{name:24} {item['pages']} páginas de {index.page_size} B")

        page = args.pagina
        raw = index._vocab.read_page(page)
        print(f"\n{VOCAB_FILE} página {page} (primeros bytes):\n{_hex(raw)}")
        if 1 <= page <= index.vocab_pages:
            count, _ = VOCAB_PAGE_HEADER.unpack_from(raw)
            print(f"  {count} entradas <term, df, cf, offset, nbytes>; primeras 5:")
            for i in range(min(5, count)):
                term, df, cf, offset, nbytes = VOCAB_ENTRY.unpack_from(
                    raw, VOCAB_PAGE_HEADER.size + i * VOCAB_ENTRY.size)
                print(f"    {_unpad(term).decode():20} df={df:<6} cf={cf:<6} "
                      f"offset={offset:<9} nbytes={nbytes}")

        if args.termino:
            term = index.analyzer.tokens(args.termino)
            term = term[0] if term else args.termino
            before = index.counter.disk_reads
            found = index.lookup(term)
            if found is None:
                print(f"\n'{term}' no está en el vocabulario")
                return
            postings = index.postings(found)
            first = 1 + found.offset // index.page_size
            print(f"\ntérmino '{term}': df={found.df}, {POSTINGS_FILE} offset={found.offset} "
                  f"(página {first}), {found.nbytes} B, {index.counter.disk_reads - before} lecturas")
            print("  primeros posteos <doc_id, tf>:", postings[:10])
            docs = index.documents([d for d, _ in postings[:3]])
            print(f"  en {DOCS_FILE} <rid, norma, longitud> ({DOC_ENTRY.size} B c/u):")
            for doc_id, (rid, norm, length) in docs.items():
                print(f"    doc {doc_id}: rid={rid} norma={norm:.4f} longitud={length}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m textual")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("dir")
    b.add_argument("csv")
    b.add_argument("--columna", default="cuerpo")
    b.add_argument("--id", default="id")
    b.add_argument("--idioma", default="spanish")
    s = sub.add_parser("search")
    s.add_argument("dir")
    s.add_argument("consulta")
    s.add_argument("-k", type=int, default=10)
    i = sub.add_parser("inspect")
    i.add_argument("dir")
    i.add_argument("--termino")
    i.add_argument("--pagina", type=int, default=1)
    a = sub.add_parser("analyze")
    a.add_argument("texto")
    args = parser.parse_args(argv)
    if args.cmd == "build":
        _build(args)
    elif args.cmd == "search":
        _search(args)
    elif args.cmd == "inspect":
        if not os.path.exists(os.path.join(args.dir, VOCAB_FILE)):
            sys.exit(f"{args.dir} no contiene un {VOCAB_FILE}")
        _inspect(args)
    else:
        for step in get_analyzer().trace(args.texto):
            print(f"  {step['token']:18} -> {step['result'] or '-':14} ({step['reason']})")


if __name__ == "__main__":
    main()
