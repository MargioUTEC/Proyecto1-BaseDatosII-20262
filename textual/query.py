# Consultas booleanas: AND, OR, NOT ('-' o '!'), paréntesis. Precedencia NOT > AND > OR.
# Sin operadores los términos se unen con OR (texto libre); 'a -b' se lee como a AND NOT b.
# Las mezclas trabajan sobre listas de doc_id ordenadas con dos punteros: O(n + m).

from __future__ import annotations

import re
from dataclasses import dataclass

from .preprocess import Analyzer


class QuerySyntaxError(ValueError):
    pass


@dataclass(frozen=True)
class Term:
    text: str


@dataclass(frozen=True)
class And:
    left: object
    right: object


@dataclass(frozen=True)
class Or:
    left: object
    right: object


@dataclass(frozen=True)
class Not:
    operand: object


Node = Term | And | Or | Not

_LEXEME = re.compile(r"\s*(\(|\)|&&|\|\||&|\||!|-(?=\S)|[^\s()&|!]+)")
_AND = {"AND", "&", "&&"}
_OR = {"OR", "|", "||"}
_NOT = {"NOT", "!", "-"}


@dataclass(frozen=True)
class ParsedQuery:
    tree: Node | None
    boolean: bool          # hubo operadores explícitos
    source: str

    def positive_terms(self) -> list[str]:
        # Términos que suman al ranking (los que no están bajo un NOT).
        found: list[str] = []
        _collect(self.tree, False, found)
        return found

    def all_terms(self) -> list[str]:
        found: list[str] = []
        _collect(self.tree, None, found)
        return found

    def render(self) -> str:
        return _render(self.tree) if self.tree is not None else "(vacía)"


def parse_query(text: str, analyzer: Analyzer) -> ParsedQuery:
    # Parsea la consulta y pasa cada término por el mismo pipeline del índice.
    lexemes = _lex(text)
    parser = _Parser(lexemes)
    tree = parser.parse()
    resolved = _resolve(tree, analyzer)
    return ParsedQuery(resolved, parser.saw_operator, text)


def _lex(text: str) -> list[str]:
    out, position = [], 0
    text = text or ""
    while position < len(text):
        match = _LEXEME.match(text, position)
        if match is None:
            if text[position:].strip():
                raise QuerySyntaxError(f"carácter inesperado en la consulta: {text[position]!r}")
            break
        out.append(match.group(1))
        position = match.end()
    return out


class _Parser:
    def __init__(self, lexemes: list[str]):
        self._lexemes = lexemes
        self._position = 0
        self.saw_operator = False

    def parse(self) -> Node | None:
        if not self._lexemes:
            return None
        node = self._or()
        if self._position != len(self._lexemes):
            raise QuerySyntaxError(f"texto sobrante en la consulta: {self._peek()!r}")
        return node

    def _peek(self) -> str | None:
        return self._lexemes[self._position] if self._position < len(self._lexemes) else None

    def _take(self) -> str:
        lexeme = self._lexemes[self._position]
        self._position += 1
        return lexeme

    def _is(self, group: set[str]) -> bool:
        lexeme = self._peek()
        return lexeme is not None and lexeme.upper() in group

    def _or(self) -> Node:
        node = self._and()
        while True:
            if self._is(_OR):
                self._take()
                self.saw_operator = True
                node = Or(node, self._and())
            elif self._is(_NOT):
                node = And(node, self._and())  # 'acceso -denegado' = acceso AND NOT denegado
            elif self._peek() is not None and self._peek() != ")":
                node = Or(node, self._and())  # yuxtaposición = OR
            else:
                return node

    def _and(self) -> Node:
        node = self._not()
        while self._is(_AND):
            self._take()
            self.saw_operator = True
            node = And(node, self._not())
        return node

    def _not(self) -> Node:
        if self._is(_NOT):
            self._take()
            self.saw_operator = True
            return Not(self._not())
        return self._atom()

    def _atom(self) -> Node:
        lexeme = self._peek()
        if lexeme is None:
            raise QuerySyntaxError("la consulta termina donde se esperaba un término")
        if lexeme == "(":
            self._take()
            node = self._or()
            if self._peek() != ")":
                raise QuerySyntaxError("falta ')' en la consulta")
            self._take()
            return node
        if lexeme == ")" or lexeme.upper() in _AND | _OR:
            raise QuerySyntaxError(f"se esperaba un término y se encontró {lexeme!r}")
        self._take()
        return Term(lexeme)


def _resolve(node: Node | None, analyzer: Analyzer) -> Node | None:
    # Reemplaza cada palabra por sus raíces; las stopwords desaparecen.
    if node is None:
        return None
    if isinstance(node, Term):
        stems = analyzer.tokens(node.text)
        if not stems:
            return None
        tree: Node = Term(stems[0])
        for stem in stems[1:]:
            tree = And(tree, Term(stem))
        return tree
    if isinstance(node, Not):
        inner = _resolve(node.operand, analyzer)
        return None if inner is None else Not(inner)
    left = _resolve(node.left, analyzer)
    right = _resolve(node.right, analyzer)
    if left is None:
        return right
    if right is None:
        return left
    return type(node)(left, right)


def _collect(node, negated, found: list[str]) -> None:
    if node is None:
        return
    if isinstance(node, Term):
        if negated is None or not negated:
            found.append(node.text)
    elif isinstance(node, Not):
        _collect(node.operand, None if negated is None else not negated, found)
    else:
        _collect(node.left, negated, found)
        _collect(node.right, negated, found)


def _render(node) -> str:
    if isinstance(node, Term):
        return node.text
    if isinstance(node, Not):
        return f"NOT {_render(node.operand)}"
    op = "AND" if isinstance(node, And) else "OR"
    return f"({_render(node.left)} {op} {_render(node.right)})"


# -- mezcla lineal de listas de posteo -------------------------------------


def intersect(a: list[int], b: list[int]) -> list[int]:
    # AND: dos punteros sobre listas ordenadas, O(n + m).
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            out.append(a[i])
            i += 1
            j += 1
        elif a[i] < b[j]:
            i += 1
        else:
            j += 1
    return out


def union(a: list[int], b: list[int]) -> list[int]:
    # OR: mezcla de dos listas ordenadas sin duplicados, O(n + m).
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            out.append(a[i])
            i += 1
            j += 1
        elif a[i] < b[j]:
            out.append(a[i])
            i += 1
        else:
            out.append(b[j])
            j += 1
    out.extend(a[i:])
    out.extend(b[j:])
    return out


def difference(a: list[int], b: list[int]) -> list[int]:
    # AND-NOT: elementos de ``a`` que no están en ``b``, O(n + m).
    out, i, j = [], 0, 0
    while i < len(a):
        if j >= len(b) or a[i] < b[j]:
            out.append(a[i])
            i += 1
        elif a[i] == b[j]:
            i += 1
            j += 1
        else:
            j += 1
    return out


def evaluate(node: Node | None, postings_of, universe) -> list[int]:
    # Evalúa el árbol con mezclas lineales.
    if node is None:
        return []
    if isinstance(node, Term):
        return postings_of(node.text)
    if isinstance(node, And):
        if isinstance(node.right, Not):
            return difference(evaluate(node.left, postings_of, universe),
                              evaluate(node.right.operand, postings_of, universe))
        if isinstance(node.left, Not):
            return difference(evaluate(node.right, postings_of, universe),
                              evaluate(node.left.operand, postings_of, universe))
        return intersect(evaluate(node.left, postings_of, universe),
                         evaluate(node.right, postings_of, universe))
    if isinstance(node, Or):
        return union(evaluate(node.left, postings_of, universe),
                     evaluate(node.right, postings_of, universe))
    if isinstance(node, Not):
        return difference(universe(), evaluate(node.operand, postings_of, universe))
    raise TypeError(f"nodo desconocido {node!r}")


def matches_bag(node: Node | None, terms: set[str]) -> bool:
    # La misma semántica, evaluada sobre un solo documento (Full Text Scan).
    if node is None:
        return False
    if isinstance(node, Term):
        return node.text in terms
    if isinstance(node, And):
        return matches_bag(node.left, terms) and matches_bag(node.right, terms)
    if isinstance(node, Or):
        return matches_bag(node.left, terms) or matches_bag(node.right, terms)
    if isinstance(node, Not):
        return not matches_bag(node.operand, terms)
    raise TypeError(f"nodo desconocido {node!r}")
