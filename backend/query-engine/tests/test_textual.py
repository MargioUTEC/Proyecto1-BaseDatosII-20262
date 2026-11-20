# Pruebas del Bloque B: índice invertido, consultas booleanas, TF-IDF e integración SQL.

import math
import os
import random

import pytest

from queryengine.bootstrap import Settings, build_engine
from queryengine.errors import CatalogError, PlannerError
from queryengine.textsearch import textual

T = textual()
from textual.query import difference, intersect, union  # noqa: E402

PAGE = 4096
VOCAB = (
    "acceso camara rostro credencial sede verificacion biometrica falla red usuario "
    "visitante seguridad registro protocolo auditoria permiso alarma puerta torniquete "
    "iluminacion umbral coincidencia identidad fotografia mantenimiento"
).split()


def corpus(n, seed=7):
    rng = random.Random(seed)
    docs = []
    for i in range(n):
        words = [rng.choice(VOCAB[: 5 + (i % len(VOCAB))]) for _ in range(rng.randint(3, 25))]
        docs.append((i, " ".join(words)))
    return docs


QUERIES = [
    "acceso",
    "camara rostro",
    "acceso AND camara",
    "acceso OR falla",
    "acceso AND NOT camara",
    "(rostro OR credencial) AND sede AND NOT red",
    "NOT acceso",
    "verificacion biometrica -falla",
    "palabrainexistente",
    "el de la",  # solo stopwords
]


def same_hits(a, b):
    assert [h.key for h in a] == [h.key for h in b]
    for x, y in zip(a, b, strict=True):
        assert math.isclose(x.score, y.score, rel_tol=1e-9, abs_tol=1e-12)


# -- preprocesamiento -------------------------------------------------------


def test_analyzer_stopwords_stemming_y_tildes():
    analyzer = T.get_analyzer("spanish")
    terms = analyzer.tokens("Las CÁMARAS de la sede detectaron rostros; la cámara falló 2 veces.")
    assert "las" not in terms and "de" not in terms and "la" not in terms
    assert terms.count(analyzer.tokens("cámaras")[0]) == 2  # cámaras y cámara, misma raíz
    assert all(term == T.preprocess.strip_accents(term) for term in terms)
    assert "2" not in terms


def test_trace_explica_cada_palabra():
    steps = T.get_analyzer().trace("el acceso")
    assert steps[0]["reason"] == "stopword" and steps[1]["reason"] == "raíz"


# -- consultas booleanas ----------------------------------------------------


def test_mezclas_lineales():
    a, b = [1, 3, 5, 7, 9], [3, 4, 5, 10]
    assert intersect(a, b) == [3, 5]
    assert union(a, b) == [1, 3, 4, 5, 7, 9, 10]
    assert difference(a, b) == [1, 7, 9]


def test_precedencia_y_yuxtaposicion():
    analyzer = T.get_analyzer()
    assert T.parse_query("a1x OR b2x AND c3x", analyzer).render().startswith("(a1x OR (b2x AND")
    free = T.parse_query("acceso camara", analyzer)
    assert not free.boolean and " OR " in free.render()
    minus = T.parse_query("acceso -camara", analyzer)
    assert minus.boolean and "AND NOT" in minus.render()


def test_error_de_sintaxis():
    with pytest.raises(T.QuerySyntaxError):
        T.parse_query("(acceso AND", T.get_analyzer())


# -- indice en disco ---------------------------------------------------------


@pytest.fixture
def built(tmp_path):
    docs = corpus(1500)
    counter = T.DiskCounter()
    index = T.InvertedIndex.build(str(tmp_path / "idx"), docs, counter=counter,
                                  block_postings=2_000)
    yield index, docs, counter
    index.close()


def test_spimi_usa_varios_bloques_y_archivos_paginados(built):
    index, docs, _ = built
    assert index.last_build.spimi_blocks > 1
    assert index.n_docs == len(docs)
    for name in ("vocab.dic", "postings.inv", "docs.bin"):
        size = os.path.getsize(os.path.join(index.directory, name))
        assert size % PAGE == 0 and size > 0
    assert not os.path.exists(os.path.join(index.directory, ".staging"))


@pytest.mark.parametrize("query", QUERIES)
def test_indice_igual_a_full_text_scan(built, query):
    index, docs, _ = built
    same_hits(index.search(query, top_k=None).hits, T.full_text_scan(docs, query, top_k=None))
    same_hits(index.search(query, top_k=10).hits, T.full_text_scan(docs, query, top_k=10))


def test_ubicar_un_termino_cuesta_una_lectura(built):
    index, _, counter = built
    before = counter.disk_reads
    info = index.lookup("acces")
    assert info is not None and counter.disk_reads - before == 1
    assert index.lookup("zzzzzz") is None


def test_lista_de_posteo_lee_solo_sus_paginas(built):
    index, _, counter = built
    info = index.lookup("acces")
    before = counter.disk_reads
    postings = index.postings(info)
    first, last = info.offset // PAGE, (info.offset + info.nbytes - 1) // PAGE
    assert counter.disk_reads - before == last - first + 1
    assert len(postings) == info.df
    assert [d for d, _ in postings] == sorted(d for d, _ in postings)


def test_normas_precalculadas(built):
    index, docs, _ = built
    analyzer = index.analyzer
    bag = analyzer.bag(docs[42][1])
    expected = math.sqrt(sum(((1 + math.log10(tf)) * index.idf(index.lookup(t).df)) ** 2
                             for t, tf in bag.items()))
    assert math.isclose(index.documents([42])[42][1], expected, rel_tol=1e-9)


def test_persistencia_al_reabrir(built):
    index, _, _ = built
    expected = index.search("acceso camara", 5).hits
    index.close()
    again = T.InvertedIndex(index.directory)
    try:
        same_hits(again.search("acceso camara", 5).hits, expected)
    finally:
        again.close()


def test_agregar_documentos_equivale_a_reconstruir(tmp_path):
    docs = corpus(600, seed=3)
    incremental = T.InvertedIndex.build(str(tmp_path / "inc"), docs[:400], block_postings=900)
    incremental.add(docs[400:], block_postings=900)
    full = T.InvertedIndex.build(str(tmp_path / "full"), docs)
    try:
        assert incremental.n_docs == full.n_docs == 600
        for query in QUERIES:
            same_hits(incremental.search(query, None).hits, full.search(query, None).hits)
    finally:
        incremental.close()
        full.close()


def test_bolsas_precontadas_para_bovw(tmp_path):
    bags = [(i, {f"vw{(i * 7 + j) % 50}": j + 1 for j in range(5)}) for i in range(200)]
    index = T.InvertedIndex.build(str(tmp_path / "bovw"), bags)
    try:
        assert index.lookup("vw3") is not None
        assert index.search("vw3 vw10", 5).hits
    finally:
        index.close()


# -- integracion SQL ---------------------------------------------------------


@pytest.fixture
def text_engine(tmp_path):
    engine = build_engine(Settings(backend="memory", catalog_path=str(tmp_path / "cat.json"),
                                   table_dir=str(tmp_path / "tables")))
    engine.execute("CREATE TABLE notas (id INT PRIMARY KEY, tipo CHAR(8), cuerpo VARCHAR(400));")
    rows = [f"({i}, 't{i % 3}', '{text}')" for i, text in corpus(400, seed=11)]
    engine.execute(f"INSERT INTO notas VALUES {', '.join(rows)};")
    return engine


SQL = ("SELECT id, COSINE_SCORE() AS rel FROM notas WHERE cuerpo MATCH '{q}' "
       "ORDER BY rel DESC LIMIT 10;")


@pytest.mark.parametrize("query", ["acceso camara", "acceso AND NOT camara", "rostro OR red"])
def test_sql_con_y_sin_indice_coinciden(text_engine, query):
    sin_indice = text_engine.execute(SQL.format(q=query))
    assert sin_indice.plan_text.count("FullTextScan") == 1
    text_engine.execute("CREATE INDEX ix_cuerpo ON notas(cuerpo) USING INVERTED "
                        "WITH (scoring = 'TFIDF_COSINE');")
    con_indice = text_engine.execute(SQL.format(q=query))
    assert "InvertedIndexScan" in con_indice.plan_text
    assert con_indice.rows == sin_indice.rows
    assert con_indice.columns == ("id", "rel")


def test_sql_post_filtering_y_orden_ascendente(text_engine):
    text_engine.execute("CREATE INDEX ix ON notas(cuerpo) USING INVERTED;")
    result = text_engine.execute("SELECT id, tipo, COSINE_SCORE() AS rel FROM notas "
                                 "WHERE cuerpo MATCH 'acceso' AND tipo = 't1' "
                                 "ORDER BY rel ASC LIMIT 5;")
    assert "post-filtering" in result.plan_text
    assert all(row[1] == "t1" for row in result.rows)
    scores = [row[2] for row in result.rows]
    assert scores == sorted(scores)


def test_sql_inserciones_y_borrados_despues_del_indice(text_engine):
    text_engine.execute("CREATE INDEX ix ON notas(cuerpo) USING INVERTED;")
    text_engine.execute("INSERT INTO notas VALUES (9001, 'nuevo', 'xilofono xilofono acceso');")
    hit = text_engine.execute("SELECT id FROM notas WHERE cuerpo MATCH 'xilofono';")
    assert hit.rows == [(9001,)]
    text_engine.execute("DELETE FROM notas WHERE id = 9001;")
    assert text_engine.execute("SELECT id FROM notas WHERE cuerpo MATCH 'xilofono';").rows == []


def test_sql_en_disco_reabre_el_indice_persistido(tmp_path):
    blk01 = pytest.importorskip("queryengine.storage.blk01")
    try:
        blk01.load()
    except Exception as exc:  # pragma: no cover - depende del checkout
        pytest.skip(f"capa fisica no disponible: {exc}")
    settings = Settings(backend="disk", catalog_path=str(tmp_path / "cat.json"),
                        data_dir=str(tmp_path), table_dir=str(tmp_path / "tables"))
    engine = build_engine(settings)
    engine.execute("CREATE TABLE notas (id INT PRIMARY KEY, cuerpo VARCHAR(400));")
    rows = [f"({i}, '{text}')" for i, text in corpus(300, seed=5)]
    engine.execute(f"INSERT INTO notas VALUES {', '.join(rows)};")
    engine.execute("CREATE INDEX ix ON notas(cuerpo) USING INVERTED;")
    expected = engine.execute(SQL.format(q="acceso camara"))
    assert "InvertedIndexScan" in expected.plan_text

    again = build_engine(settings)  # otro proceso: catalogo y archivos desde disco
    result = again.execute(SQL.format(q="acceso camara"))
    assert "InvertedIndexScan" in result.plan_text
    assert result.rows == expected.rows
    assert again.text_index("ix")["documents"] == 300


def test_sql_errores(text_engine):
    with pytest.raises(PlannerError):
        text_engine.execute("SELECT id, COSINE_SCORE() FROM notas WHERE id = 1;")
    with pytest.raises(PlannerError):
        text_engine.execute("SELECT id FROM notas WHERE cuerpo MATCH '(acceso';")
    with pytest.raises(CatalogError):
        text_engine.execute("CREATE INDEX ix ON notas(id) USING INVERTED;")
    with pytest.raises(CatalogError):
        text_engine.execute("CREATE INDEX ix ON notas(cuerpo) USING INVERTED WITH (scoring='BM25');")
