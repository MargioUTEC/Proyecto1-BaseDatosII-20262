"""Access path selection.

The planner turns a bound statement into a physical plan. Its only real job is
the one the assignment names: look at the WHERE clause, enumerate the access
paths the catalog makes available, price each one, and keep the cheapest.

    equality + HASH or BTREE index        -> IndexScan
    range    + BTREE index                -> IndexRangeScan
    primary key on a SEQUENTIAL table     -> SequentialSearch / SequentialRangeScan
    anything else                         -> SeqScan

Whatever the chosen path does not enforce is re-checked by a Filter above it, so
the plan is correct regardless of which path wins.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..catalog import Catalog, IndexKind, IndexMeta, StorageKind, TableSchema
from ..errors import PlannerError
from ..sql import ast
from ..storage.port import IndexManager
from ..types import TypeKind
from . import cost
from .binder import bind_expression, bind_projection
from .plan import (
    DDLPlan,
    DeletePlan,
    Filter,
    IndexRangeScan,
    IndexScan,
    InsertPlan,
    Limit,
    PlanNode,
    Project,
    RTreeKNNScan,
    RTreeRadiusScan,
    SeqScan,
    SequentialRangeScan,
    SequentialSearch,
    Sort,
    TextSearch,
    VectorSearch,
)
from .predicates import KeyRange, as_sargable, key_ranges, split_conjuncts

K_POR_DEFECTO = 5
FACTOR_CANDIDATOS = 4
MARGEN_RECALL = 2
MAX_CANDIDATOS = 500


@dataclass
class _Candidate:
    node: PlanNode
    consumed: list[ast.Expression]


class Planner:
    def __init__(self, catalog: Catalog, indexes: IndexManager | None = None):
        self._catalog = catalog
        self._indexes = indexes

    def plan(self, statement: ast.Statement) -> PlanNode:
        if isinstance(statement, ast.Select):
            return self._plan_select(statement)
        if isinstance(statement, ast.Delete):
            return self._plan_delete(statement)
        if isinstance(statement, ast.Insert):
            return self._plan_insert(statement)
        if isinstance(statement, ast.CreateTable):
            return DDLPlan(
                operation="CreateTable",
                target=statement.table,
                attributes={"storage": statement.storage, "columns": len(statement.columns)},
            )
        if isinstance(statement, ast.DropTable):
            return DDLPlan(operation="DropTable", target=statement.table)
        if isinstance(statement, ast.CreateIndex):
            return DDLPlan(
                operation="CreateIndex",
                target=statement.name,
                attributes={
                    "on": f"{statement.table}({statement.column})",
                    "using": statement.kind,
                },
            )
        if isinstance(statement, ast.DropIndex):
            return DDLPlan(operation="DropIndex", target=statement.name)
        if isinstance(statement, ast.Copy):
            schema = self._catalog.table(statement.table)
            return DDLPlan(
                operation="Copy",
                target=schema.name,
                attributes={
                    "from": statement.path,
                    "indexes": len(self._catalog.indexes_on(schema.name)),
                },
            )
        raise PlannerError(f"no hay plan para {type(statement).__name__}")

    # -- statements -----------------------------------------------------

    def _plan_select(self, statement: ast.Select) -> PlanNode:
        schema = self._catalog.table(statement.table)
        projection = bind_projection(_columns_only(statement), schema)
        if (statement.order_by is not None and statement.order_by.spatial_column is not None
                and statement.where is not None):
            raise PlannerError("k-NN espacial no admite un filtro WHERE adicional")
        if isinstance(statement.where, ast.WithinRadius):
            column = self._spatial_column(schema, statement.where.column)
            index = self._spatial_index(schema, column)
            rows = max(1, round(self._catalog.statistics(schema.name).row_count * 0.1))
            pages = schema.page_count(self._catalog.statistics(schema.name).row_count)
            estimate = cost.CostEstimate(
                float((self._height(index) + rows) if index else pages), rows,
                "R-Tree MINDIST y lecturas de tabla por RID" if index else "escaneo de tabla y filtro por radio",
            )
            node = RTreeRadiusScan(table=schema.name, index=index.name if index else None,
                                   column=column, point=statement.where.point,
                                   radius=statement.where.radius, estimate=estimate)
        elif statement.order_by is not None and statement.order_by.spatial_column is not None:
            column = self._spatial_column(schema, statement.order_by.spatial_column)
            if statement.limit is None:
                raise PlannerError("ORDER BY DISTANCE requiere LIMIT para k-NN")
            index = self._spatial_index(schema, column)
            rows = min(statement.limit, self._catalog.statistics(schema.name).row_count)
            estimate = cost.CostEstimate(
                float((self._height(index) + rows) if index else
                      schema.page_count(self._catalog.statistics(schema.name).row_count)),
                rows, "R-Tree k-NN por MINDIST" if index else "escaneo secuencial para k-NN",
            )
            node = RTreeKNNScan(table=schema.name, index=index.name if index else None,
                                column=column, point=statement.order_by.point, count=statement.limit,
                                estimate=estimate)
        else:
            where = bind_expression(statement.where, schema)
            vectorial = self._vector_search(schema, statement, where)
            if vectorial is not None:
                return vectorial
            textual = self._text_search(schema, statement, where)
            if textual is not None:
                return textual
            if statement.score_alias is not None:
                raise PlannerError("COSINE_SCORE() requiere un predicado 'columna MATCH ...' en el WHERE")
            node = self._access_path(schema, where)
        if statement.order_by is not None and statement.order_by.spatial_column is None:
            column = schema.column(statement.order_by.column).name
            node = Sort(child=node, column=column, descending=statement.order_by.descending)
        if statement.limit is not None:
            node = Limit(child=node, count=statement.limit)
        if projection != schema.column_names:
            node = Project(child=node, columns=projection)
        return node

    # -- recuperacion vectorial -------------------------------------------

    def _vector_search(self, schema: TableSchema, statement: ast.Select,
                       where: ast.Expression | None) -> PlanNode | None:
        """Decide entre pre y post filtering, que es lo que el enunciado pide reportar.

        Pre-filtering: la via relacional acota las filas y el IVF solo puntua esas,
        asi que cuesta lo que cuesta el filtro. Post-filtering: el IVF sondea sus
        listas y el filtro se aplica despues, asi que cuesta lo que cuesta el indice.
        Gana la mas barata, y TopK deja dicho cual corrio.
        """
        consulta = statement.vector_query
        if consulta is None:
            return None
        meta = next((m for m in self._catalog.indexes_on(schema.name, consulta.column)
                     if m.kind is IndexKind.IVF), None)
        if meta is None:
            raise PlannerError(
                f"'{consulta.column} <=> ...' necesita un indice IVF sobre "
                f"{schema.name}({consulta.column})"
            )

        conjuncts = split_conjuncts(where)
        if any(isinstance(c, ast.Match) for c in conjuncts):
            raise PlannerError("no se puede combinar MATCH con una distancia vectorial")
        order = statement.order_by
        if order is not None and order.spatial_column is not None:
            raise PlannerError("no se puede combinar ORDER BY DISTANCE con <=>")
        por_similitud = order is not None and order.column.lower() == consulta.alias.lower()
        if order is not None and not por_similitud:
            raise PlannerError(
                f"una consulta vectorial se ordena por su alias; usa ORDER BY {consulta.alias}"
            )
        top_k = statement.limit
        ascendente = bool(por_similitud and not order.descending)

        estadisticas = self._catalog.statistics(schema.name)
        candidatos = self._candidatos_ivf(meta, top_k, estadisticas.row_count)
        relacional = self._access_path(schema, where) if conjuncts else None
        filas_pre = _filas_estimadas(conjuncts, estadisticas)
        pedidas = max(1, top_k or K_POR_DEFECTO)
        holgura = pedidas * MARGEN_RECALL
        tope = self._tope_ivf(meta)
        entregables = min(tope, candidatos)
        sobreviven = (None if filas_pre is None
                      else _supervivientes(entregables, filas_pre, estadisticas.row_count))

        if relacional is not None and (sobreviven is None or sobreviven < holgura):
            acotadas = estadisticas.row_count if filas_pre is None else filas_pre
            razon = (f"{relacional.estimate.rationale}; el IVF puntua esas "
                     f"{acotadas} filas. ")
            razon += ("No hay estadisticas del filtro, y el post-filtering sin saber "
                      "su selectividad devolveria menos filas de las pedidas"
                      if sobreviven is None else
                      f"Del indice sobrevivirian {sobreviven}, y hacen falta "
                      f"{holgura} para responder {pedidas} con margen")
            estimacion = cost.CostEstimate(
                relacional.estimate.blocks + acotadas,
                min(acotadas, top_k or acotadas),
                razon,
            )
            return self._rematar(VectorSearch(
                table=schema.name, index=meta.name, column=consulta.column,
                vector=consulta.vector, alias=consulta.alias, child=relacional,
                top_k=top_k, ascending=ascendente,
                strategy="pre-filtering mediante la via relacional",
                estimate=estimacion,
            ), statement, schema, residual=[])

        nprobe = int(meta.option("nprobe") or 0) or None
        emite = _emision(top_k, conjuncts, filas_pre, estadisticas.row_count, entregables)
        estimacion = cost.CostEstimate(
            float(candidatos + emite),
            top_k or K_POR_DEFECTO,
            f"el IVF sondea sus listas y devuelve {candidatos} candidatos "
            f"+ {emite} lecturas por RID",
        )
        nodo = VectorSearch(
            table=schema.name, index=meta.name, column=consulta.column,
            vector=consulta.vector, alias=consulta.alias, top_k=top_k,
            emit=emite, ascending=ascendente, nprobe=nprobe,
            strategy=("post-filtering sobre los candidatos del IVF" if conjuncts
                      else "ranking directo sobre el IVF"),
            estimate=estimacion,
        )
        return self._rematar(nodo, statement, schema, residual=conjuncts)

    def _tope_ivf(self, meta: IndexMeta) -> int:
        """El maximo de filas que el indice entrega de una consulta.

        Importa porque es el techo del post-filtering: el filtro solo puede
        trabajar sobre lo que el indice devolvio, no sobre todo lo que sondeo.
        """
        por_opcion = int(meta.option("max_candidates") or 0)
        techo = getattr(self._indexes, "result_cap", None)
        if techo is not None:
            try:
                del_servicio = techo(meta.name)
            except Exception:  # noqa: BLE001 - sin el servicio queda la opcion del indice
                del_servicio = None
            if del_servicio:
                return del_servicio
        return por_opcion or MAX_CANDIDATOS

    def _candidatos_ivf(self, meta: IndexMeta, top_k: int | None, filas: int) -> int:
        """Cuantos vectores mira la via del indice, que es con lo que se compara el filtro.

        El indice sondea nprobe de sus listas, asi que toca una fraccion de la
        coleccion que no depende de k: con 128 listas y nprobe 4 son unas 400 de
        13 000 filas. Suponer k * FACTOR_CANDIDATOS en su lugar lo subestima por
        un orden de magnitud en cuanto la tabla crece, y entonces el
        pre-filtering no gana nunca.
        """
        minimo = max(1, (top_k or K_POR_DEFECTO)) * FACTOR_CANDIDATOS
        estimar = getattr(self._indexes, "candidate_estimate", None)
        if estimar is None:
            return minimo
        try:
            sondeados = estimar(meta.name, filas)
        except Exception:  # noqa: BLE001 - sin el servicio se usa la cota de abajo
            return minimo
        return max(minimo, sondeados) if sondeados else minimo

    def _rematar(self, nodo: PlanNode, statement: ast.Select, schema: TableSchema,
                 residual: list) -> PlanNode:
        fuente = nodo
        if residual:
            predicate = _rebuild_conjunction(residual)
            nodo = Filter(child=nodo, predicate=predicate, text=_render(predicate),
                          estimate=nodo.estimate)
        if statement.limit is not None:
            nodo = Limit(child=nodo, count=statement.limit)
        if statement.projection is not None and statement.projection != schema.column_names:
            nodo = Project(child=nodo, columns=statement.projection,
                           score_alias=statement.vector_query.alias, score_source=fuente)
        return nodo

    # -- recuperacion textual ---------------------------------------------

    def _text_search(self, schema: TableSchema, statement: ast.Select,
                     where: ast.Expression | None) -> PlanNode | None:
        # El primer MATCH del WHERE define el acceso (índice o Full Text Scan); el resto va en un
        # Filter (post-filtering). Si se ordena por el puntaje sin filtro, el LIMIT es el K del Top-K.
        from ..textsearch import check_query

        conjuncts = split_conjuncts(where)
        match = next((c for c in conjuncts if isinstance(c, ast.Match)), None)
        if match is None:
            return None
        residual = [c for c in conjuncts if c is not match]
        meta = next((m for m in self._catalog.indexes_on(schema.name, match.column)
                     if m.kind is IndexKind.INVERTED), None)
        language = meta.option("language") if meta else None
        parsed = check_query(match.query, language)

        alias = statement.score_alias
        order = statement.order_by
        if order is not None and order.spatial_column is not None:
            raise PlannerError("no se puede combinar MATCH con ORDER BY DISTANCE")
        by_score = order is not None and alias is not None and order.column.lower() == alias.lower()
        top_k = (statement.limit if statement.limit is not None and not residual
                 and (order is None or by_score) else None)

        statistics = self._catalog.statistics(schema.name)
        pages = schema.page_count(statistics.row_count)
        rows = top_k or max(1, statistics.row_count // 100)
        terms = len(set(parsed.all_terms()))
        if meta is not None:
            estimate = cost.CostEstimate(
                float(2 * terms + 1 + rows), rows,
                f"{terms} pagina(s) de vocab.dic + listas de posteo + normas en docs.bin "
                f"+ {rows} lecturas por RID",
            )
        else:
            estimate = cost.CostEstimate(
                float(pages), rows, f"lee las {pages} paginas y analiza cada texto (sin indice)"
            )
        text_node = TextSearch(
            table=schema.name, index=meta.name if meta else None, column=match.column,
            query=match.query, parsed=parsed.render(), top_k=top_k,
            ascending=by_score and not order.descending, language=language,
            strategy="post-filtering sobre los candidatos" if residual else "",
            estimate=estimate,
        )
        node: PlanNode = text_node
        if residual:
            predicate = _rebuild_conjunction(residual)
            node = Filter(child=node, predicate=predicate, text=_render(predicate),
                          estimate=estimate)
        if order is not None and not by_score:
            column = schema.column(order.column).name
            node = Sort(child=node, column=column, descending=order.descending)
        if statement.limit is not None:
            node = Limit(child=node, count=statement.limit)
        if statement.projection is None:
            columns = schema.column_names
        else:
            columns = tuple(name if alias is not None and name == alias
                            else schema.column(name).name for name in statement.projection)
        if alias is not None or columns != schema.column_names:
            node = Project(child=node, columns=columns, score_alias=alias, score_source=text_node)
        return node

    def _spatial_index(self, schema: TableSchema, column: str) -> IndexMeta | None:
        return next((meta for meta in self._catalog.indexes_on(schema.name)
                     if meta.kind is IndexKind.RTREE
                     and meta.column.lower() == column.lower()), None)

    @staticmethod
    def _spatial_column(schema: TableSchema, name: str) -> str:
        column = schema.column(name)
        if column.type.kind not in (TypeKind.CHAR, TypeKind.VARCHAR):
            raise PlannerError("la columna espacial debe ser CHAR/VARCHAR con formato POINT(lon lat)")
        return column.name

    def _plan_delete(self, statement: ast.Delete) -> PlanNode:
        schema = self._catalog.table(statement.table)
        where = bind_expression(statement.where, schema)
        touched = tuple(meta.name for meta in self._catalog.indexes_on(schema.name))
        return DeletePlan(
            table=schema.name,
            child=self._access_path(schema, where),
            index_updates=touched,
        )

    def _plan_insert(self, statement: ast.Insert) -> PlanNode:
        schema = self._catalog.table(statement.table)
        touched = tuple(meta.name for meta in self._catalog.indexes_on(schema.name))
        return InsertPlan(
            table=schema.name,
            row_count=len(statement.rows),
            index_updates=touched,
        )

    # -- access path selection ------------------------------------------

    def _access_path(self, schema: TableSchema, where: ast.Expression | None) -> PlanNode:
        statistics = self._catalog.statistics(schema.name)
        conjuncts = split_conjuncts(where)
        ranges = key_ranges(conjuncts)

        candidates = [self._full_scan(schema, statistics)]
        for key_range in ranges.values():
            if key_range.empty:
                continue
            candidates.extend(self._indexed_paths(schema, statistics, key_range, conjuncts))
            candidates.extend(self._ordered_paths(schema, statistics, key_range, conjuncts))

        best = min(candidates, key=lambda candidate: candidate.node.estimate.blocks)
        residual = [conjunct for conjunct in conjuncts if conjunct not in best.consumed]
        if not residual:
            return best.node
        predicate = _rebuild_conjunction(residual)
        return Filter(
            child=best.node,
            predicate=predicate,
            text=_render(predicate),
            estimate=best.node.estimate,
        )

    def _full_scan(self, schema: TableSchema, statistics) -> _Candidate:
        estimate = cost.seq_scan(schema, statistics)
        return _Candidate(SeqScan(table=schema.name, estimate=estimate), consumed=[])

    def _indexed_paths(
        self,
        schema: TableSchema,
        statistics,
        key_range: KeyRange,
        conjuncts: list[ast.Expression],
    ) -> list[_Candidate]:
        found: list[_Candidate] = []
        rows = cost.expected_rows(key_range, statistics)
        consumed = _conjuncts_for(conjuncts, key_range.column)
        for meta in self._catalog.indexes_on(schema.name, key_range.column):
            if meta.kind in (IndexKind.RTREE, IndexKind.INVERTED):
                continue
            height = self._height(meta)
            if key_range.is_equality:
                estimate = cost.index_equality(height, rows, meta.kind is IndexKind.HASH)
                found.append(
                    _Candidate(
                        IndexScan(
                            table=schema.name,
                            index=meta.name,
                            kind=meta.kind,
                            column=key_range.column,
                            key=key_range.equality,
                            estimate=estimate,
                        ),
                        consumed,
                    )
                )
            elif key_range.is_bounded and meta.kind.supports_range:
                estimate = cost.index_range(height, rows)
                found.append(
                    _Candidate(
                        IndexRangeScan(
                            table=schema.name,
                            index=meta.name,
                            column=key_range.column,
                            lower=key_range.lower,
                            upper=key_range.upper,
                            lower_inclusive=key_range.lower_inclusive,
                            upper_inclusive=key_range.upper_inclusive,
                            estimate=estimate,
                        ),
                        consumed,
                    )
                )
        return found

    def _ordered_paths(
        self,
        schema: TableSchema,
        statistics,
        key_range: KeyRange,
        conjuncts: list[ast.Expression],
    ) -> list[_Candidate]:
        primary = schema.primary_key
        if schema.storage is not StorageKind.SEQUENTIAL or primary is None:
            return []
        if primary.name.lower() != key_range.column.lower():
            return []
        rows = cost.expected_rows(key_range, statistics)
        consumed = _conjuncts_for(conjuncts, key_range.column)
        if key_range.is_equality:
            estimate = cost.binary_search(schema, statistics, rows)
            return [
                _Candidate(
                    SequentialSearch(
                        table=schema.name,
                        column=primary.name,
                        key=key_range.equality,
                        estimate=estimate,
                    ),
                    consumed,
                )
            ]
        if key_range.is_bounded:
            estimate = cost.sequential_range(schema, statistics, rows)
            return [
                _Candidate(
                    SequentialRangeScan(
                        table=schema.name,
                        column=primary.name,
                        lower=key_range.lower,
                        upper=key_range.upper,
                        estimate=estimate,
                    ),
                    consumed,
                )
            ]
        return []

    def _height(self, meta: IndexMeta) -> int:
        if self._indexes is None:
            return 3  # textbook default for a tree that has not reported yet
        try:
            return max(1, self._indexes.height(meta.name))
        except Exception:
            return 3


def _supervivientes(candidatos: int, filas_filtradas: int, filas_tabla: int) -> int:
    """Cuantas de las filas que sondea el IVF pasarian el filtro relacional.

    Es la cuenta que decide la estrategia. Si de lo que el indice alcanza a
    mirar sobreviven menos de las k pedidas, el post-filtering no puede
    responder la consulta completa por barato que sea, y hay que acotar antes
    aunque eso cueste recorrer la tabla.
    """
    if filas_tabla <= 0:
        return candidatos
    return math.floor(candidatos * max(1, filas_filtradas) / filas_tabla)


def _emision(top_k: int | None, conjuncts: list, filas_filtradas: int | None,
             filas_tabla: int, tope: int) -> int:
    """Cuantas filas tiene que emitir el IVF para que al final queden las k pedidas.

    En post-filtering el filtro relacional corre encima del ranking, asi que de
    las filas que emite el indice solo sobrevive la fraccion que pasa el
    predicado. Emitiendo k exactas la consulta devuelve menos de las pedidas: con
    un filtro que deja el 4% de la tabla y k=5, salen una o dos.

    Lo que se emite es k dividido por esa fraccion, por el margen: cuantas
    sobreviven es un sorteo alrededor de ese valor, asi que pedir lo justo falla
    la mitad de las veces. El tope es lo que el indice admite devolver, porque
    mas alla de ahi no hay candidatos que pedir.
    """
    pedidas = max(1, top_k or K_POR_DEFECTO)
    if not conjuncts or filas_tabla <= 0 or filas_filtradas is None:
        return pedidas
    fraccion = max(1, filas_filtradas) / filas_tabla
    holgado = math.ceil(pedidas / fraccion) * MARGEN_RECALL
    return min(max(tope, pedidas), max(pedidas, holgado))


def _filas_estimadas(conjuncts: list, estadisticas) -> int | None:
    """Cuantas filas deja el filtro relacional, o None si no hay con que saberlo.

    No sirve mirar las filas del plan: un SeqScan con un Filter encima sigue
    diciendo que devuelve toda la tabla, porque el filtro no cambia su
    estimacion. Lo que decide entre pre y post filtering es la selectividad del
    predicado, asi que se calcula aqui sobre los rangos sargables.

    Cuando la cuenta no aporta nada, porque no sale ningun rango o porque sin
    estadisticas de la columna el estimador acaba devolviendo la tabla entera,
    esto contesta None en lugar de un numero. La diferencia importa: dar por
    bueno que el filtro no reduce nada lleva a elegir post-filtering, y si el
    filtro resulta selectivo la consulta devuelve menos filas de las pedidas,
    o ninguna, sin avisar.
    """
    if not conjuncts:
        return estadisticas.row_count
    rangos = [r for r in key_ranges(conjuncts).values() if not r.empty]
    if not rangos:
        return None
    estimadas = min(cost.expected_rows(rango, estadisticas) for rango in rangos)
    return None if estimadas >= estadisticas.row_count else estimadas


def _columns_only(statement: ast.Select) -> tuple[str, ...] | None:
    # La proyección sin los alias que calcula el plan: COSINE_SCORE() y la distancia.
    if statement.projection is None:
        return None
    calculados = {statement.score_alias}
    if statement.vector_query is not None:
        calculados.add(statement.vector_query.alias)
    calculados.discard(None)
    if not calculados:
        return statement.projection
    return tuple(name for name in statement.projection if name not in calculados)


def _conjuncts_for(conjuncts: list[ast.Expression], column: str) -> list[ast.Expression]:
    """The conjuncts an access path on ``column`` fully enforces."""
    target = column.lower()
    matched = []
    for conjunct in conjuncts:
        predicate = as_sargable(conjunct)
        if predicate is not None and predicate.column.lower() == target:
            matched.append(conjunct)
    return matched


def _rebuild_conjunction(conjuncts: list[ast.Expression]) -> ast.Expression:
    node = conjuncts[0]
    for extra in conjuncts[1:]:
        node = ast.Logical(ast.LogicalOp.AND, node, extra)
    return node


def _render(expression: ast.Expression) -> str:
    """Readable form of a predicate, for the plan shown in the client."""
    if isinstance(expression, ast.Logical):
        left, right = _render(expression.left), _render(expression.right)
        return f"({left} {expression.operator.value} {right})"
    if isinstance(expression, ast.Not):
        return f"NOT {_render(expression.operand)}"
    if isinstance(expression, ast.Compare):
        return f"{_render(expression.left)} {expression.operator.value} {_render(expression.right)}"
    if isinstance(expression, ast.Between):
        negation = "NOT " if expression.negated else ""
        return (
            f"{_render(expression.operand)} {negation}BETWEEN "
            f"{_render(expression.lower)} AND {_render(expression.upper)}"
        )
    if isinstance(expression, ast.IsNull):
        return f"{_render(expression.operand)} IS {'NOT ' if expression.negated else ''}NULL"
    if isinstance(expression, ast.Match):
        return f"{expression.column} MATCH {expression.query!r}"
    if isinstance(expression, ast.ColumnRef):
        return expression.name
    if isinstance(expression, ast.Literal):
        return repr(expression.value)
    return str(expression)
