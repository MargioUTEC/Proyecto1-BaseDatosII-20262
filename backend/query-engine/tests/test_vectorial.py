"""El camino vectorial del motor, con un doble del bloque C.

El bloque C corre como servicio aparte y necesita opencv y los modelos, asi que
aqui se sustituye por un indice en memoria que contesta lo mismo: candidatos de
un IVF y similitudes de un conjunto de ids. Lo que se prueba es lo que le toca
al motor, que es elegir entre pre y post filtering y reportar cual corrio.
"""

import pytest

from queryengine.bootstrap import Settings, build_engine
from queryengine.errors import PlannerError

ROSTROS = [
    (1, 7, "00000/00001.png", "[1.0, 0.0, 0.0]"),
    (2, 7, "00000/00002.png", "[0.9, 0.1, 0.0]"),
    (3, 9, "00000/00003.png", "[0.0, 1.0, 0.0]"),
    (4, 9, "00000/00004.png", "[0.0, 0.9, 0.1]"),
    (5, 3, "00000/00005.png", "[0.0, 0.0, 1.0]"),
]
CONSULTA = "[1.0, 0.0, 0.0]"


def coseno(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


class BloqueCFalso:
    """Responde como el servicio del bloque C, sin red ni modelos."""

    def __init__(self, listas: int = 0):
        self.vectores = {}
        self.listas = listas
        self.llamadas = {"search": 0, "score": 0}

    def cargar(self, rutas_y_vectores):
        self.vectores = dict(rutas_y_vectores)

    @property
    def rutas(self):
        return tuple(self.vectores)

    def buscar(self, vector, modo, k, nprobe, max_candidatos):
        self.llamadas["search"] += 1
        filas = sorted(((ruta, coseno(vec, vector)) for ruta, vec in self.vectores.items()),
                       key=lambda par: -par[1])[:k]
        return {"results": [{"id": i, "path": ruta, "similarity": sim}
                            for i, (ruta, sim) in enumerate(filas)],
                "candidates": len(self.vectores),
                "collection_size": len(self.vectores),
                "selectivity": 1.0, "search_ms": 0.1}

    def puntuar(self, vector, modo, ids):
        self.llamadas["score"] += 1
        rutas = self.rutas
        return {"scores": [{"id": int(i), "similarity": coseno(self.vectores[rutas[i]], vector)}
                           for i in ids],
                "candidates": len(ids), "search_ms": 0.1}


@pytest.fixture
def bloque_c(monkeypatch):
    falso = BloqueCFalso()
    falso.cargar((ruta, [float(v) for v in vector.strip("[]").split(",")])
                 for _, _, ruta, vector in ROSTROS)
    from queryengine import vectorsearch

    monkeypatch.setattr(vectorsearch, "buscar", falso.buscar)
    monkeypatch.setattr(vectorsearch, "puntuar", falso.puntuar)
    monkeypatch.setattr(vectorsearch, "rutas_indexadas", lambda firma: falso.rutas)
    monkeypatch.setattr(vectorsearch, "estado", lambda: {"face_clusters": falso.listas})
    return falso


@pytest.fixture
def rostros(bloque_c, tmp_path):
    engine = build_engine(Settings(backend="memory", catalog_path=None,
                                   table_dir=str(tmp_path)))
    engine.execute(
        "CREATE TABLE rostros ("
        "  id INT PRIMARY KEY,"
        "  identidad_id INT,"
        "  embedding VARCHAR(60)"
        ") USING HEAP;"
    )
    valores = ", ".join(f"({i}, {ident}, '{ruta}')" for i, ident, ruta, _ in ROSTROS)
    engine.execute(f"INSERT INTO rostros VALUES {valores};")
    engine.execute(
        "CREATE INDEX ix_vec ON rostros (embedding) USING IVF "
        "WITH (mode = 'face', nprobe = 2);"
    )
    return engine


def test_sin_indice_ivf_la_consulta_se_rechaza_con_una_razon(bloque_c, tmp_path):
    engine = build_engine(Settings(backend="memory", catalog_path=None,
                                   table_dir=str(tmp_path)))
    engine.execute("CREATE TABLE t (id INT PRIMARY KEY, embedding VARCHAR(60)) USING HEAP;")
    engine.execute("INSERT INTO t VALUES (1, '00000/00001.png');")
    with pytest.raises(PlannerError) as fallo:
        engine.execute(f"SELECT id, embedding <=> '{CONSULTA}' AS d FROM t ORDER BY d DESC;")
    assert "IVF" in str(fallo.value)


def test_el_ranking_vectorial_devuelve_las_filas_mas_parecidas(rostros):
    resultado = rostros.execute(
        f"SELECT id, embedding <=> '{CONSULTA}' AS dist FROM rostros "
        "ORDER BY dist DESC LIMIT 3;"
    )
    assert resultado.columns == ("id", "dist")
    assert [fila[0] for fila in resultado.rows] == [1, 2, 3]
    assert resultado.rows[0][1] > resultado.rows[1][1]


def test_sin_filtro_relacional_el_ivf_rankea_directo(rostros, bloque_c):
    plan = rostros.execute(
        f"SELECT id, embedding <=> '{CONSULTA}' AS dist FROM rostros "
        "ORDER BY dist DESC LIMIT 3;"
    ).plan_text
    assert "IVFScan" in plan
    assert "ranking directo" in plan
    assert bloque_c.llamadas["search"] == 1


def test_un_filtro_selectivo_elige_pre_filtering(rostros, bloque_c):
    resultado = rostros.execute(
        f"SELECT id, embedding <=> '{CONSULTA}' AS dist FROM rostros "
        "WHERE identidad_id = 9 ORDER BY dist DESC LIMIT 2;"
    )
    assert "pre-filtering" in resultado.plan_text
    assert "IVFRefine" in resultado.plan_text
    assert bloque_c.llamadas["score"] == 1
    assert [fila[0] for fila in resultado.rows] == [3, 4]


def test_el_pre_filtering_solo_puntua_las_filas_que_sobrevivieron(rostros):
    resultado = rostros.execute(
        f"SELECT id, embedding <=> '{CONSULTA}' AS dist FROM rostros "
        "WHERE identidad_id = 3 ORDER BY dist DESC LIMIT 5;"
    )
    assert [fila[0] for fila in resultado.rows] == [5]


def test_la_telemetria_separa_el_indice_de_la_tabla(rostros):
    plan = rostros.execute(
        f"SELECT id, embedding <=> '{CONSULTA}' AS dist FROM rostros "
        "ORDER BY dist DESC LIMIT 3;"
    ).plan
    nodo = plan
    while nodo.get("node") not in ("IVFScan", "IVFRefine"):
        nodo = nodo["children"][0]
    runtime = nodo["runtime"]
    assert set(runtime) >= {"index_reads", "table_reads", "index_ms", "table_ms", "candidates"}
    assert runtime["returned"] == 3


def test_ordenar_por_algo_que_no_es_el_alias_se_rechaza(rostros):
    with pytest.raises(PlannerError):
        rostros.execute(
            f"SELECT id, embedding <=> '{CONSULTA}' AS dist FROM rostros ORDER BY id DESC;"
        )


def test_no_se_mezcla_match_con_distancia_vectorial(rostros):
    with pytest.raises(PlannerError):
        rostros.execute(
            f"SELECT id, embedding <=> '{CONSULTA}' AS dist FROM rostros "
            "WHERE embedding MATCH 'algo' ORDER BY dist DESC;"
        )


def test_un_vector_mal_escrito_es_un_error_de_sintaxis(rostros):
    from queryengine.errors import SQLSyntaxError

    with pytest.raises(SQLSyntaxError):
        rostros.execute("SELECT id, embedding <=> '[1.0, hola]' AS d FROM rostros ORDER BY d;")


def test_en_disco_el_indice_ivf_se_reabre_tras_reiniciar(bloque_c, tmp_path):
    """El IVF no guarda nada de su lado: lo que se reconstruye es el mapa ruta -> RID.

    Sin eso el motor arranca con el indice en el catalogo pero vacio, y la
    consulta vectorial no encuentra ninguna fila que devolver.
    """
    blk01 = pytest.importorskip("queryengine.storage.blk01")
    try:
        blk01.load()
    except Exception as exc:  # pragma: no cover - depende del checkout
        pytest.skip(f"capa fisica no disponible: {exc}")

    settings = Settings(backend="disk", catalog_path=str(tmp_path / "cat.json"),
                        data_dir=str(tmp_path), table_dir=str(tmp_path / "tables"))
    consulta = (f"SELECT id, embedding <=> '{CONSULTA}' AS dist FROM rostros "
                "ORDER BY dist DESC LIMIT 3;")

    engine = build_engine(settings)
    engine.execute("CREATE TABLE rostros ("
                   "  id INT PRIMARY KEY,"
                   "  identidad_id INT,"
                   "  embedding VARCHAR(60)"
                   ") USING HEAP;")
    valores = ", ".join(f"({i}, {ident}, '{ruta}')" for i, ident, ruta, _ in ROSTROS)
    engine.execute(f"INSERT INTO rostros VALUES {valores};")
    engine.execute("CREATE INDEX ix_vec ON rostros (embedding) USING IVF "
                   "WITH (mode = 'face', nprobe = 2);")
    esperado = engine.execute(consulta)
    assert esperado.rows

    otro = build_engine(settings)  # otro proceso: catalogo y tabla desde disco
    resultado = otro.execute(consulta)
    assert "IVFScan" in resultado.plan_text
    assert resultado.rows == esperado.rows


def test_las_listas_del_ivf_mueven_la_frontera_entre_pre_y_post(bloque_c, tmp_path):
    """Lo que decide no es k, es cuanto de la coleccion alcanza a sondear el indice.

    Con pocas listas el IVF mira muchos vectores, asi que de los que devuelve
    sobreviven suficientes al filtro y conviene dejarlo rankear. Con muchas
    listas mira una porcion minima, de ahi no salen las k filas pedidas, y hay
    que acotar antes aunque cueste recorrer la tabla.
    """
    filas = 200
    bloque_c.cargar((f"lote/{i:05}.png", [float(i), 1.0, 0.0]) for i in range(filas))

    def plan_con(listas: int) -> str:
        bloque_c.listas = listas
        engine = build_engine(Settings(backend="memory", catalog_path=None,
                                       table_dir=str(tmp_path / str(listas))))
        engine.execute("CREATE TABLE caras (id INT PRIMARY KEY, "
                       "embedding VARCHAR(60)) USING HEAP;")
        valores = ", ".join(f"({i}, 'lote/{i:05}.png')" for i in range(filas))
        engine.execute(f"INSERT INTO caras VALUES {valores};")
        engine.execute("CREATE INDEX ix_vec ON caras (embedding) USING IVF "
                       "WITH (mode = 'face', nprobe = 1);")
        return engine.execute(
            f"SELECT id, embedding <=> '{CONSULTA}' AS dist FROM caras "
            "WHERE id < 30 ORDER BY dist DESC LIMIT 5;"
        ).plan_text

    pocas = plan_con(1)       # una lista sola: mira las 200 y del filtro pasan 30
    assert "post-filtering" in pocas and "IVFScan" in pocas

    muchas = plan_con(filas)  # una lista por fila: pasarian 3 de las 10 que hacen falta
    assert "pre-filtering" in muchas and "IVFRefine" in muchas


def test_post_filtering_devuelve_las_k_filas_pedidas(bloque_c, tmp_path):
    """El filtro corre encima del ranking, asi que el indice tiene que emitir de mas.

    Pidiendo k exactas al IVF, el filtro se come la mayoria y la consulta
    responde con una o dos filas en lugar de cinco.
    """
    filas = 400
    bloque_c.cargar((f"lote/{i:05}.png", [float(filas - i), 1.0, 0.0])
                    for i in range(filas))
    bloque_c.listas = 1
    engine = build_engine(Settings(backend="memory", catalog_path=None,
                                   table_dir=str(tmp_path)))
    engine.execute("CREATE TABLE caras (id INT PRIMARY KEY, "
                   "embedding VARCHAR(60)) USING HEAP;")
    valores = ", ".join(f"({i}, 'lote/{i:05}.png')" for i in range(filas))
    engine.execute(f"INSERT INTO caras VALUES {valores};")
    engine.execute("CREATE INDEX ix_vec ON caras (embedding) USING IVF "
                   "WITH (mode = 'face', nprobe = 1);")

    resultado = engine.execute(
        f"SELECT id, embedding <=> '{CONSULTA}' AS dist FROM caras "
        "WHERE id < 40 ORDER BY dist DESC LIMIT 5;")
    assert "post-filtering" in resultado.plan_text
    assert len(resultado.rows) == 5
    assert all(fila[0] < 40 for fila in resultado.rows)
