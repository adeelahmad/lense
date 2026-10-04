"""A read-only subset of Cypher (openCypher, the basis of ISO GQL) over the archive's property graph (graph_model.py).

Agents and people query the graph in the language models already write best. Lens parses and runs the query itself,
over a projection that holds only the namespaces the caller can read, so a query can never reach anything else, and
it never changes data (CREATE, MERGE, SET, DELETE and REMOVE are refused: changes go through graph changes).

Supported:

- MATCH and OPTIONAL MATCH with comma-separated patterns: (n:Label:Other {prop: value}), -[r:TYPE|OTHER]->, <-[]-,
  -[]- (either direction), variable length -[*]-, -[*2]-, -[*1..3]- (at most MAX_HOPS), named paths p = (...),
  shortestPath(...) and allShortestPaths(...). A relationship is used once per match, as in Cypher.
- WHERE with AND, OR, XOR, NOT, = <> < > <= >=, =~ (regex), IN, STARTS WITH, ENDS WITH, CONTAINS, IS [NOT] NULL,
  arithmetic, lists, maps, $parameters, list comprehensions [x IN list WHERE ... | ...], CASE.
- WITH and RETURN with aliases, DISTINCT, * , aggregation (count, sum, avg, min, max, collect; count(*), DISTINCT
  inside), ORDER BY ... ASC|DESC, SKIP, LIMIT; UNWIND; UNION [ALL].
- Functions: id, labels, type, properties, keys, startNode, endNode, nodes, relationships, length, size, head, last,
  coalesce, toLower, toUpper, lower, upper, trim, toString, toInteger, toFloat, split, substring, replace, left,
  right, abs, round, floor, ceil, exists, range, reverse.

A query runs within a budget (steps and seconds) and returns at most `max_rows` rows, so a careless pattern fails with
a clear message instead of tying up the server.
"""

from __future__ import annotations

import math
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .graph_model import Graph, Node, Rel

MAX_HOPS = 8
DEFAULT_ROWS = 500
MAX_STEPS = 3_000_000
MAX_SECONDS = 8.0
WRITE_WORDS = {"CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP", "LOAD", "CALL", "FOREACH"}


class CypherError(ValueError):
    """A query Lens can't run: the message says what and where, for the person or agent to fix it."""


class TooBig(CypherError):
    pass


@dataclass
class Path:
    nodes: list[str]
    rels: list[Rel]

    def __hash__(self):
        return hash((tuple(self.nodes), tuple(r.id for r in self.rels)))

    def __eq__(self, other):
        return isinstance(other, Path) and self.nodes == other.nodes and [r.id for r in self.rels] == [r.id for r in other.rels]


# ---------- tokens ----------
_TOKEN = re.compile(
    r"""
    (?P<ws>\s+|//[^\n]*|/\*.*?\*/)
  | (?P<num>\d+\.\d+(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?|\d+(?:[eE][-+]?\d+)?)
  | (?P<str>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*|`(?:[^`]|``)+`)
  | (?P<param>\$[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\.\.|<>|!=|<=|>=|=~|->|<-|[-+*/%^=<>(){}\[\]:,.|;])
    """,
    re.X | re.S,
)
_ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}


@dataclass
class Tok:
    kind: str
    text: str
    pos: int

    @property
    def up(self):
        return self.text.upper() if self.kind == "name" else self.text


def tokenize(src: str) -> list[Tok]:
    out, i = [], 0
    while i < len(src):
        m = _TOKEN.match(src, i)
        if not m:
            raise CypherError(f"unexpected character {src[i]!r} at position {i}")
        kind = m.lastgroup
        text = m.group()
        if kind == "str":
            body = text[1:-1]
            text = re.sub(r"\\(.)", lambda x: _ESC.get(x.group(1), x.group(1)), body)
        elif kind == "name" and text.startswith("`"):
            text, kind = text[1:-1].replace("``", "`"), "qname"
        if kind != "ws":
            out.append(Tok(kind, text, m.start()))
        i = m.end()
    out.append(Tok("eof", "", len(src)))
    return out


# ---------- the syntax tree ----------
@dataclass
class NodePat:
    var: str | None
    labels: list[list[str]]  # all of the groups, any label within a group (:A|B)
    props: dict[str, Any] | None


@dataclass
class RelPat:
    var: str | None
    types: list[str]
    direction: str  # out, in, both
    min: int = 1
    max: int = 1
    props: dict[str, Any] | None = None
    varlen: bool = False


@dataclass
class Pattern:
    path_var: str | None
    nodes: list[NodePat]
    rels: list[RelPat]
    shortest: str | None = None  # "one" or "all"


@dataclass
class Match:
    patterns: list[Pattern]
    where: Any
    optional: bool


@dataclass
class Item:
    expr: Any
    alias: str


@dataclass
class Projection:
    items: list[Item]
    star: bool
    distinct: bool
    order: list[tuple[Any, bool]]
    skip: Any
    limit: Any
    where: Any = None
    final: bool = False


@dataclass
class Unwind:
    expr: Any
    var: str


@dataclass
class Query:
    parts: list[Any] = field(default_factory=list)


AGGREGATES = {"count", "sum", "avg", "min", "max", "collect"}


class Parser:
    def __init__(self, src: str):
        self.src = src
        self.toks = tokenize(src)
        self.i = 0
        self.anon = 0

    # helpers
    @property
    def t(self) -> Tok:
        return self.toks[self.i]

    def peek(self, k=1) -> Tok:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def at(self, *words) -> bool:
        return self.t.up in words and self.t.kind in ("name", "op")

    def take(self, *words) -> Tok | None:
        if self.at(*words):
            tok = self.t
            self.i += 1
            return tok
        return None

    def need(self, *words) -> Tok:
        tok = self.take(*words)
        if not tok:
            self.fail(f"expected {' or '.join(words)}")
        return tok

    def fail(self, msg):
        t = self.t
        near = self.src[max(0, t.pos - 20) : t.pos + 20].replace("\n", " ")
        got = "the end of the query" if t.kind == "eof" else repr(t.text)
        raise CypherError(f"{msg}, found {got} at position {t.pos} (near “{near}”)")

    def ident(self) -> str:
        if self.t.kind in ("name", "qname"):
            tok = self.t
            self.i += 1
            return tok.text
        self.fail("expected a name")

    def fresh(self):
        self.anon += 1
        return f" anon{self.anon}"

    # query
    def parse(self) -> list[tuple[Query, bool]]:
        for t in self.toks:
            if t.kind == "name" and t.up in WRITE_WORDS:
                raise CypherError(
                    f"{t.up} isn't allowed: graph queries are read-only. To change the graph, propose a change "
                    "(POST /api/v1/graph/changes, or the propose_graph_change tool)."
                )
        queries = [(self.single(), False)]
        while self.take("UNION"):
            all_ = bool(self.take("ALL"))
            queries.append((self.single(), all_))
        self.take(";")
        if self.t.kind != "eof":
            self.fail("expected the end of the query")
        return queries

    def single(self) -> Query:
        q = Query()
        while True:
            if self.at("OPTIONAL"):
                self.i += 1
                self.need("MATCH")
                q.parts.append(self.match(True))
            elif self.take("MATCH"):
                q.parts.append(self.match(False))
            elif self.take("UNWIND"):
                e = self.expr()
                self.need("AS")
                q.parts.append(Unwind(e, self.ident()))
            elif self.take("WITH"):
                p = self.projection(False)
                if self.take("WHERE"):
                    p.where = self.expr()
                q.parts.append(p)
            elif self.take("RETURN"):
                q.parts.append(self.projection(True))
                return q
            else:
                self.fail("expected MATCH, OPTIONAL MATCH, WITH, UNWIND or RETURN")

    def match(self, optional) -> Match:
        pats = [self.pattern()]
        while self.take(","):
            pats.append(self.pattern())
        where = self.expr() if self.take("WHERE") else None
        return Match(pats, where, optional)

    def pattern(self) -> Pattern:
        var = None
        if self.t.kind in ("name", "qname") and self.peek().text == "=":
            var = self.ident()
            self.need("=")
        if self.at("SHORTESTPATH", "ALLSHORTESTPATHS"):
            kind = "one" if self.t.up == "SHORTESTPATH" else "all"
            self.i += 1
            self.need("(")
            p = self.chain()
            self.need(")")
            if len(p.rels) != 1:
                raise CypherError("shortestPath takes one relationship pattern, like shortestPath((a)-[*..6]-(b))")
            p.shortest, p.path_var = kind, var
            if not p.rels[0].varlen:
                p.rels[0].varlen, p.rels[0].min, p.rels[0].max = True, 1, MAX_HOPS
            return p
        p = self.chain()
        p.path_var = var
        return p

    def chain(self) -> Pattern:
        nodes, rels = [self.node()], []
        while self.at("-", "<-"):
            rels.append(self.rel())
            nodes.append(self.node())
        return Pattern(None, nodes, rels)

    def node(self) -> NodePat:
        self.need("(")
        var = self.ident() if self.t.kind in ("name", "qname") else None
        labels = []
        while self.take(":"):
            group = [self.ident()]
            while self.take("|"):
                self.take(":")
                group.append(self.ident())
            labels.append(group)
        props = self.map_literal() if self.at("{") else None
        self.need(")")
        return NodePat(var, labels, props)

    def rel(self) -> RelPat:
        left = bool(self.take("<-")) or (self.need("-") and False)
        var, types, lo, hi, props, varlen = None, [], 1, 1, None, False
        if self.take("["):
            var = self.ident() if self.t.kind in ("name", "qname") else None
            if self.take(":"):
                types.append(self.ident())
                while self.take("|"):
                    self.take(":")
                    types.append(self.ident())
            if self.take("*"):
                varlen, lo, hi = True, 1, MAX_HOPS
                if self.t.kind == "num":
                    lo = hi = int(self.t.text)
                    self.i += 1
                if self.take(".."):
                    hi = MAX_HOPS
                    if self.t.kind == "num":
                        hi = int(self.t.text)
                        self.i += 1
                if hi > MAX_HOPS:
                    raise CypherError(f"variable-length relationships go up to {MAX_HOPS} hops")
                if lo > hi:
                    raise CypherError("the shortest length is longer than the longest")
            props = self.map_literal() if self.at("{") else None
            self.need("]")
        right = bool(self.take("->")) or (self.need("-") and False)
        if left and right:
            raise CypherError("a relationship points one way: use -> or <-, or - for either")
        direction = "in" if left else "out" if right else "both"
        return RelPat(var, types, direction, lo, hi, props, varlen)

    def map_literal(self) -> dict[str, Any]:
        self.need("{")
        out = {}
        if not self.at("}"):
            while True:
                k = self.ident()
                self.need(":")
                out[k] = self.expr()
                if not self.take(","):
                    break
        self.need("}")
        return out

    def projection(self, final) -> Projection:
        distinct = bool(self.take("DISTINCT"))
        items, star = [], False
        if self.take("*"):
            star = True
            if self.take(","):
                items = self.items()
        else:
            items = self.items()
        order, skip, limit = [], None, None
        if self.take("ORDER"):
            self.need("BY")
            while True:
                e = self.expr()
                desc = bool(self.take("DESC", "DESCENDING"))
                if not desc:
                    self.take("ASC", "ASCENDING")
                order.append((e, desc))
                if not self.take(","):
                    break
        if self.take("SKIP"):
            skip = self.expr()
        if self.take("LIMIT"):
            limit = self.expr()
        return Projection(items, star, distinct, order, skip, limit, final=final)

    def items(self) -> list[Item]:
        out = []
        while True:
            start = self.t.pos
            e = self.expr()
            end = self.t.pos
            alias = self.ident() if self.take("AS") else self.src[start:end].strip()
            out.append(Item(e, alias))
            if not self.take(","):
                return out

    # expressions: tuples ("op", ...) evaluated by Runner.ev
    def expr(self):
        left = self.xor()
        while self.take("OR"):
            left = ("or", left, self.xor())
        return left

    def xor(self):
        left = self.and_()
        while self.take("XOR"):
            left = ("xor", left, self.and_())
        return left

    def and_(self):
        left = self.not_()
        while self.take("AND"):
            left = ("and", left, self.not_())
        return left

    def not_(self):
        if self.take("NOT"):
            return ("not", self.not_())
        return self.compare()

    def compare(self):
        left = self.add()
        while True:
            if self.at("=", "<>", "!=", "<", ">", "<=", ">=", "=~"):
                op = self.t.text
                self.i += 1
                left = ("cmp", "<>" if op == "!=" else op, left, self.add())
            elif self.take("IN"):
                left = ("in", left, self.add())
            elif self.at("STARTS") and self.peek().up == "WITH":
                self.i += 2
                left = ("starts", left, self.add())
            elif self.at("ENDS") and self.peek().up == "WITH":
                self.i += 2
                left = ("ends", left, self.add())
            elif self.take("CONTAINS"):
                left = ("contains", left, self.add())
            elif self.at("IS"):
                self.i += 1
                neg = bool(self.take("NOT"))
                self.need("NULL")
                left = ("isnull", left, neg)
            else:
                return left

    def add(self):
        left = self.mul()
        while self.at("+", "-"):
            op = self.t.text
            self.i += 1
            left = ("arith", op, left, self.mul())
        return left

    def mul(self):
        left = self.power()
        while self.at("*", "/", "%"):
            op = self.t.text
            self.i += 1
            left = ("arith", op, left, self.power())
        return left

    def power(self):
        left = self.unary()
        if self.take("^"):
            return ("arith", "^", left, self.power())
        return left

    def unary(self):
        if self.take("-"):
            return ("neg", self.unary())
        if self.take("+"):
            return self.unary()
        return self.postfix()

    def postfix(self):
        e = self.atom()
        while True:
            if self.take("."):
                e = ("prop", e, self.ident())
            elif self.at("["):
                self.i += 1
                if self.take(".."):
                    hi = self.expr()
                    self.need("]")
                    e = ("slice", e, None, hi)
                    continue
                lo = self.expr()
                if self.take(".."):
                    hi = None if self.at("]") else self.expr()
                    self.need("]")
                    e = ("slice", e, lo, hi)
                else:
                    self.need("]")
                    e = ("index", e, lo)
            else:
                return e

    def atom(self):
        t = self.t
        if t.kind == "num":
            self.i += 1
            return ("lit", float(t.text) if any(c in t.text for c in ".eE") else int(t.text))
        if t.kind == "str":
            self.i += 1
            return ("lit", t.text)
        if t.kind == "param":
            self.i += 1
            return ("param", t.text[1:])
        if self.take("("):
            e = self.expr()
            self.need(")")
            return e
        if self.at("["):
            return self.list_or_comprehension()
        if self.at("{"):
            return ("map", self.map_literal())
        if t.kind == "name":
            up = t.up
            if up in ("TRUE", "FALSE"):
                self.i += 1
                return ("lit", up == "TRUE")
            if up == "NULL":
                self.i += 1
                return ("lit", None)
            if up == "CASE":
                return self.case()
            if self.peek().text == "(":
                return self.call()
        if t.kind in ("name", "qname"):
            self.i += 1
            return ("var", t.text)
        self.fail("expected a value")

    def list_or_comprehension(self):
        self.need("[")
        if self.t.kind in ("name", "qname") and self.peek().up == "IN" and self.peek().kind == "name":
            var = self.ident()
            self.need("IN")
            src = self.expr()
            cond = self.expr() if self.take("WHERE") else None
            out = self.expr() if self.take("|") else None
            self.need("]")
            return ("comp", var, src, cond, out)
        items = []
        if not self.at("]"):
            while True:
                items.append(self.expr())
                if not self.take(","):
                    break
        self.need("]")
        return ("list", items)

    def case(self):
        self.need("CASE")
        subject = None if self.at("WHEN") else self.expr()
        whens = []
        while self.take("WHEN"):
            w = self.expr()
            self.need("THEN")
            whens.append((w, self.expr()))
        other = self.expr() if self.take("ELSE") else None
        self.need("END")
        return ("case", subject, whens, other)

    def call(self):
        name = self.ident().lower()
        self.need("(")
        if name == "count" and self.take("*"):
            self.need(")")
            return ("agg", "count", None, False)
        distinct = bool(self.take("DISTINCT"))
        args = []
        if not self.at(")"):
            while True:
                args.append(self.expr())
                if not self.take(","):
                    break
        self.need(")")
        if name in AGGREGATES:
            if len(args) != 1:
                raise CypherError(f"{name}() takes one argument")
            return ("agg", name, args[0], distinct)
        if name in ("exists",) and len(args) == 1:
            return ("fn", "exists", args)
        if name not in FUNCTIONS:
            raise CypherError(f"unknown function {name}(); Lens knows {', '.join(sorted(FUNCTIONS | AGGREGATES))}")
        return ("fn", name, args)


# ---------- values ----------
def _str(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(v)
    if isinstance(v, Node):
        return v.id
    return str(v)


def _int(v):
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _num(v, name):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise CypherError(f"{name}() needs a number")
    return v


def _seq(v, name):
    if v is None:
        return None
    if isinstance(v, Path):
        return v.nodes
    if not isinstance(v, (list, str)):
        raise CypherError(f"{name}() needs a list or a string")
    return v


FUNCTIONS = {
    "id",
    "elementid",
    "labels",
    "type",
    "properties",
    "keys",
    "startnode",
    "endnode",
    "nodes",
    "relationships",
    "rels",
    "length",
    "size",
    "head",
    "last",
    "coalesce",
    "tolower",
    "toupper",
    "lower",
    "upper",
    "trim",
    "ltrim",
    "rtrim",
    "tostring",
    "tointeger",
    "toint",
    "tofloat",
    "split",
    "substring",
    "replace",
    "left",
    "right",
    "abs",
    "round",
    "floor",
    "ceil",
    "sign",
    "sqrt",
    "range",
    "reverse",
    "exists",
    "tail",
}


def _cmp_key(v):
    """Ordering across types, as Cypher sorts: maps, nodes, relationships, lists, paths, strings, booleans, numbers, null."""
    if v is None:
        return (9, 0)
    if isinstance(v, bool):
        return (6, v)
    if isinstance(v, (int, float)):
        return (7, v) if not (isinstance(v, float) and math.isnan(v)) else (8, 0)
    if isinstance(v, str):
        return (5, v)
    if isinstance(v, Node):
        return (1, v.id)
    if isinstance(v, Rel):
        return (2, v.id)
    if isinstance(v, list):
        return (3, tuple(_cmp_key(x) for x in v))
    if isinstance(v, Path):
        return (4, tuple(v.nodes))
    return (0, str(v))


def _hashable(v):
    if isinstance(v, list):
        return ("l", tuple(_hashable(x) for x in v))
    if isinstance(v, dict):
        return ("m", tuple(sorted((k, _hashable(x)) for k, x in v.items())))
    if isinstance(v, Node):
        return ("n", v.id)
    if isinstance(v, Rel):
        return ("r", v.id)
    if isinstance(v, Path):
        return ("p", tuple(v.nodes), tuple(r.id for r in v.rels))
    if isinstance(v, bool):
        return ("b", v)
    return v


def _eq(a, b):
    if a is None or b is None:
        return None
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b) and not (isinstance(a, (Node, Rel, Path)) or isinstance(b, (Node, Rel, Path))):
        return False
    return _hashable(a) == _hashable(b)


def _truth(v):
    return v is True


def has_agg(e) -> bool:
    if isinstance(e, tuple):
        if e[0] == "agg":
            return True
        if e[0] == "comp":
            return any(has_agg(x) for x in e[2:] if x is not None)
        return any(has_agg(x) for x in e[1:] if isinstance(x, (tuple, list)))
    if isinstance(e, list):
        return any(has_agg(x) for x in e)
    if isinstance(e, dict):
        return any(has_agg(x) for x in e.values())
    return False


def variables(e) -> set[str]:
    """The variables an expression reads (comprehension variables excluded)."""
    if isinstance(e, tuple):
        if e[0] == "var":
            return {e[1]}
        if e[0] == "comp":
            inner = set()
            for x in e[3:]:
                if x is not None:
                    inner |= variables(x)
            return variables(e[2]) | (inner - {e[1]})
        out = set()
        for x in e[1:]:
            if isinstance(x, (tuple, list, dict)):
                out |= variables(x)
        return out
    if isinstance(e, list):
        out = set()
        for x in e:
            out |= variables(x)
        return out
    if isinstance(e, dict):
        out = set()
        for x in e.values():
            out |= variables(x)
        return out
    return set()


def _conjuncts(e):
    if isinstance(e, tuple) and e[0] == "and":
        return _conjuncts(e[1]) + _conjuncts(e[2])
    return [e] if e is not None else []


# ---------- running ----------
class Runner:
    def __init__(self, g: Graph, params=None, max_rows=DEFAULT_ROWS, max_steps=MAX_STEPS, max_seconds=MAX_SECONDS):
        self.g = g
        self.params = params or {}
        self.max_rows = max_rows
        self.steps = 0
        self.max_steps = max_steps
        self.deadline = time.monotonic() + max_seconds
        self.max_seconds = max_seconds
        self._rx = {}

    def tick(self, n=1):
        self.steps += n
        if self.steps > self.max_steps:
            raise TooBig(
                "the query explores too much of the graph; add labels, a starting node ({name: ...}), "
                "a shorter variable-length range, or LIMIT"
            )
        if self.steps % 2048 == 0 and time.monotonic() > self.deadline:
            raise TooBig(f"the query took longer than {self.max_seconds:g} seconds; narrow it down")

    # expressions
    def ev(self, e, row):
        op = e[0]
        if op == "lit":
            return e[1]
        if op == "var":
            if e[1] not in row:
                raise CypherError(f"variable {e[1]} isn't defined here")
            return row[e[1]]
        if op == "param":
            if e[1] not in self.params:
                raise CypherError(f"parameter ${e[1]} wasn't given")
            return self.params[e[1]]
        if op == "prop":
            v = self.ev(e[1], row)
            if v is None:
                return None
            if isinstance(v, (Node, Rel)):
                if isinstance(v, Rel) and e[2] == "type":
                    return v.props.get("type", None)
                return v.props.get(e[2])
            if isinstance(v, dict):
                return v.get(e[2])
            raise CypherError(f"can't read .{e[2]} of {type(v).__name__.lower()}")
        if op == "and":
            a = self.ev(e[1], row)
            if a is False:
                return False
            b = self.ev(e[2], row)
            if b is False:
                return False
            return None if a is None or b is None else True
        if op == "or":
            a = self.ev(e[1], row)
            if a is True:
                return True
            b = self.ev(e[2], row)
            if b is True:
                return True
            return None if a is None or b is None else False
        if op == "xor":
            a, b = self.ev(e[1], row), self.ev(e[2], row)
            return None if a is None or b is None else bool(a) != bool(b)
        if op == "not":
            v = self.ev(e[1], row)
            return None if v is None else not v
        if op == "cmp":
            return self.compare(e[1], self.ev(e[2], row), self.ev(e[3], row))
        if op == "in":
            v, lst = self.ev(e[1], row), self.ev(e[2], row)
            if lst is None:
                return None
            if not isinstance(lst, list):
                raise CypherError("IN needs a list on its right")
            if v is None:
                return None if lst else False
            seen_null = False
            for x in lst:
                r = _eq(v, x)
                if r:
                    return True
                if r is None:
                    seen_null = True
            return None if seen_null else False
        if op in ("starts", "ends", "contains"):
            a, b = self.ev(e[1], row), self.ev(e[2], row)
            if not isinstance(a, str) or not isinstance(b, str):
                return None
            return a.startswith(b) if op == "starts" else a.endswith(b) if op == "ends" else b in a
        if op == "isnull":
            v = self.ev(e[1], row) is None
            return not v if e[2] else v
        if op == "arith":
            return self.arith(e[1], self.ev(e[2], row), self.ev(e[3], row))
        if op == "neg":
            v = self.ev(e[1], row)
            return None if v is None else -_num(v, "-")
        if op == "list":
            return [self.ev(x, row) for x in e[1]]
        if op == "map":
            return {k: self.ev(x, row) for k, x in e[1].items()}
        if op == "index":
            v, k = self.ev(e[1], row), self.ev(e[2], row)
            if v is None or k is None:
                return None
            if isinstance(v, (list, str)):
                k = _int(k)
                return v[k] if -len(v) <= k < len(v) else None
            if isinstance(v, dict):
                return v.get(k)
            if isinstance(v, (Node, Rel)):
                return v.props.get(k)
            raise CypherError("only lists, strings and maps can be indexed")
        if op == "slice":
            v = self.ev(e[1], row)
            if v is None:
                return None
            lo = _int(self.ev(e[2], row)) if e[2] is not None else None
            hi = _int(self.ev(e[3], row)) if e[3] is not None else None
            return v[lo:hi]
        if op == "comp":
            src = self.ev(e[2], row)
            if src is None:
                return None
            if isinstance(src, Path):
                src = [self.g.nodes[n] for n in src.nodes]
            out = []
            for x in src:
                self.tick()
                inner = {**row, e[1]: x}
                if e[3] is not None and not _truth(self.ev(e[3], inner)):
                    continue
                out.append(self.ev(e[4], inner) if e[4] is not None else x)
            return out
        if op == "case":
            if e[1] is not None:
                subject = self.ev(e[1], row)
                for w, then in e[2]:
                    if _eq(subject, self.ev(w, row)):
                        return self.ev(then, row)
            else:
                for w, then in e[2]:
                    if _truth(self.ev(w, row)):
                        return self.ev(then, row)
            return self.ev(e[3], row) if e[3] is not None else None
        if op == "fn":
            return self.fn(e[1], [self.ev(a, row) for a in e[2]] if e[1] != "exists" else e[2], row)
        if op == "agg":
            key = id(e)
            if "\0agg" in row and key in row["\0agg"]:
                return row["\0agg"][key]
            raise CypherError("aggregates (count, collect...) only go in WITH and RETURN")
        raise CypherError(f"can't evaluate {op}")

    def regex(self, pattern):
        rx = self._rx.get(pattern)
        if rx is None:
            if len(pattern) > 500:
                raise CypherError("that regular expression is too long")
            if _NESTED.search(pattern) or re.search(r"\\[1-9]", pattern):
                # (a+)+ and backreferences can take exponential time, and a regex can't be stopped halfway
                raise CypherError("that regular expression repeats a repeated group or refers back; simplify it")
            try:
                rx = self._rx[pattern] = re.compile(pattern)
            except re.error as err:
                raise CypherError(f"bad regular expression: {err}") from None
        return rx

    def compare(self, op, a, b):
        if op == "=":
            return _eq(a, b)
        if op == "<>":
            r = _eq(a, b)
            return None if r is None else not r
        if a is None or b is None:
            return None
        if op == "=~":
            if not isinstance(a, str) or not isinstance(b, str):
                return None
            return bool(self.regex(b).fullmatch(a))
        num = isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool)
        if not num and type(a) is not type(b):
            return None
        try:
            return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
        except TypeError:
            return None

    def arith(self, op, a, b):
        if a is None or b is None:
            return None
        if op == "+":
            if isinstance(a, list) or isinstance(b, list):
                return (a if isinstance(a, list) else [a]) + (b if isinstance(b, list) else [b])
            if isinstance(a, str) or isinstance(b, str):
                return f"{_str(a)}{_str(b)}"
        a, b = _num(a, op), _num(b, op)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0:
                if isinstance(a, int) and isinstance(b, int):
                    raise CypherError("division by zero")
                return math.nan
            return int(a / b) if isinstance(a, int) and isinstance(b, int) else a / b
        if op == "%":
            if b == 0:
                raise CypherError("division by zero")
            return math.fmod(a, b) if isinstance(a, float) or isinstance(b, float) else int(math.fmod(a, b))
        if op == "^":
            return float(a) ** float(b)
        raise CypherError(f"unknown operator {op}")

    def fn(self, name, args, row):
        def one():
            if len(args) != 1:
                raise CypherError(f"{name}() takes one argument")
            return args[0]

        g = self.g
        if name == "exists":
            e = args[0]
            if e[0] != "prop":
                raise CypherError("exists() takes a property, like exists(n.title)")
            return self.ev(e, row) is not None
        if name in ("id", "elementid"):
            v = one()
            return v.id if isinstance(v, (Node, Rel)) else None
        if name == "labels":
            v = one()
            return list(v.labels) if isinstance(v, Node) else None
        if name == "type":
            v = one()
            return v.type if isinstance(v, Rel) else None
        if name == "properties":
            v = one()
            return dict(v.props) if isinstance(v, (Node, Rel)) else v
        if name == "keys":
            v = one()
            return sorted(v.props) if isinstance(v, (Node, Rel)) else sorted(v) if isinstance(v, dict) else None
        if name in ("startnode", "endnode"):
            v = one()
            return g.nodes[v.start if name == "startnode" else v.end] if isinstance(v, Rel) else None
        if name == "nodes":
            v = one()
            return [g.nodes[n] for n in v.nodes] if isinstance(v, Path) else None
        if name in ("relationships", "rels"):
            v = one()
            return list(v.rels) if isinstance(v, Path) else v if isinstance(v, list) else None
        if name == "length":
            v = one()
            if isinstance(v, Path):
                return len(v.rels)
            return None if v is None else len(_seq(v, name))
        if name == "size":
            v = one()
            return None if v is None else len(_seq(v, name))
        if name in ("head", "last"):
            v = _seq(one(), name)
            return (v[0] if name == "head" else v[-1]) if v else None
        if name == "tail":
            v = _seq(one(), name)
            return v[1:] if v is not None else None
        if name == "reverse":
            v = _seq(one(), name)
            return v[::-1] if v is not None else None
        if name == "coalesce":
            return next((a for a in args if a is not None), None)
        if name in ("tolower", "lower", "toupper", "upper", "trim", "ltrim", "rtrim"):
            v = one()
            if v is None:
                return None
            if not isinstance(v, str):
                raise CypherError(f"{name}() needs a string")
            return {
                "tolower": v.lower,
                "lower": v.lower,
                "toupper": v.upper,
                "upper": v.upper,
                "trim": v.strip,
                "ltrim": v.lstrip,
                "rtrim": v.rstrip,
            }[name]()
        if name == "tostring":
            return _str(one())
        if name in ("tointeger", "toint"):
            return _int(one())
        if name == "tofloat":
            return _float(one())
        if name == "split":
            if len(args) != 2:
                raise CypherError("split() takes a string and a separator")
            return None if args[0] is None or args[1] is None else str(args[0]).split(str(args[1]))
        if name == "substring":
            if len(args) not in (2, 3) or args[0] is None:
                return None
            s, start = str(args[0]), _int(args[1]) or 0
            return s[start : start + _int(args[2])] if len(args) == 3 else s[start:]
        if name == "replace":
            if len(args) != 3:
                raise CypherError("replace() takes a string, what to find and what to put instead")
            return None if None in args else str(args[0]).replace(str(args[1]), str(args[2]))
        if name in ("left", "right"):
            if len(args) != 2 or args[0] is None:
                return None
            n = _int(args[1]) or 0
            return str(args[0])[:n] if name == "left" else str(args[0])[-n:] if n else ""
        if name in ("abs", "round", "floor", "ceil", "sign", "sqrt"):
            v = _num(args[0] if args else None, name)
            if v is None:
                return None
            if name == "round" and len(args) == 2:
                return round(float(v), _int(args[1]) or 0)
            return {
                "abs": abs,
                "round": lambda x: float(math.floor(x + 0.5)),
                "floor": lambda x: float(math.floor(x)),
                "ceil": lambda x: float(math.ceil(x)),
                "sign": lambda x: (x > 0) - (x < 0),
                "sqrt": lambda x: math.sqrt(x) if x >= 0 else math.nan,
            }[name](v)
        if name == "range":
            if len(args) not in (2, 3):
                raise CypherError("range() takes a start, an end and an optional step")
            lo, hi, step = _int(args[0]), _int(args[1]), _int(args[2]) if len(args) == 3 else 1
            if not step:
                raise CypherError("range() needs a step other than 0")
            if abs((hi - lo) // step) > 100_000:
                raise CypherError("range() is too long")
            return list(range(lo, hi + (1 if step > 0 else -1), step))
        raise CypherError(f"unknown function {name}()")

    # matching
    def node_ok(self, n: Node, pat: NodePat, row) -> bool:
        for group in pat.labels:
            if not any(lb in n.labels for lb in group):
                return False
        if pat.props:
            for k, ex in pat.props.items():
                if not _truth(_eq(n.props.get(k), self.ev(ex, row))):
                    return False
        return True

    def rel_ok(self, r: Rel, pat: RelPat, row) -> bool:
        if pat.types and r.type not in pat.types:
            return False
        if pat.props:
            for k, ex in pat.props.items():
                if not _truth(_eq(r.props.get(k), self.ev(ex, row))):
                    return False
        return True

    def candidates(self, pat: NodePat, row):
        g = self.g
        if pat.var and pat.var in row:
            v = row[pat.var]
            if v is None:
                return []
            if not isinstance(v, Node):
                raise CypherError(f"{pat.var} isn't a node")
            return [v.id] if self.node_ok(v, pat, row) else []
        if pat.props and "id" in pat.props:
            want = self.ev(pat.props["id"], row)
            try:
                nid = g.resolve(want)
            except KeyError:
                return []
            return [nid] if self.node_ok(g.nodes[nid], pat, row) else []
        pool = None
        for group in pat.labels:
            ids = [x for lb in group for x in g.by_label.get(lb, ())]
            if pool is None or len(ids) < len(pool):
                pool = ids
        pool = list(g.nodes) if pool is None else pool
        out = []
        for nid in pool:
            self.tick()
            if self.node_ok(g.nodes[nid], pat, row):
                out.append(nid)
        return out

    def estimate(self, pat: NodePat, row) -> float:
        if pat.var and pat.var in row:
            return 0
        if pat.props and "id" in pat.props:
            return 1
        size = len(self.g.nodes)
        for group in pat.labels:
            size = min(size, sum(len(self.g.by_label.get(lb, ())) for lb in group))
        return size / (20 if pat.props else 1)

    def run_match(self, m: Match, rows):
        out = []
        new_vars = []
        for p in m.patterns:
            for v in [p.path_var] + [n.var for n in p.nodes] + [r.var for r in p.rels]:
                if v and v not in new_vars:
                    new_vars.append(v)
        conj = _conjuncts(m.where)
        for row in rows:
            fresh = [v for v in new_vars if v not in row]
            got = []
            self.match_patterns(m.patterns, 0, dict(row), set(), conj, got)
            if got:
                out.extend(got)
            elif m.optional:
                out.append({**row, **{v: None for v in fresh}})
            if len(out) > self.max_rows * 50:
                raise TooBig("the match has too many results; add conditions or LIMIT")
        return out

    def ready(self, conj, row, done):
        """Evaluate the WHERE parts whose variables are all bound now; False when one fails."""
        for i, c in enumerate(conj):
            if i in done:
                continue
            if variables(c) <= row.keys():
                done.add(i)
                if not _truth(self.ev(c, row)):
                    return False
        return True

    def match_patterns(self, pats, k, row, used, conj, got, done=frozenset()):
        if k == len(pats):
            done = set(done)
            if self.ready(conj, row, done) and len(done) == len(conj):
                got.append({key: v for key, v in row.items() if not key.startswith(" ")})
            elif len(done) != len(conj):
                missing = set().union(*(variables(c) for i, c in enumerate(conj) if i not in done)) - row.keys()
                raise CypherError(f"WHERE uses {', '.join(sorted(missing))}, which isn't defined")
            return
        p = pats[k]
        mine = {n.var for n in p.nodes if n.var} | {r.var for r in p.rels if r.var}
        early = [(c, vs) for i, c in enumerate(conj) if i not in done and (vs := variables(c)) & (mine - row.keys())]
        for row2, used2 in self.match_one(p, row, used, early):
            d = set(done)
            if self.ready(conj, row2, d):
                self.match_patterns(pats, k + 1, row2, used2, conj, got, frozenset(d))

    def match_one(self, p: Pattern, row, used, early=()):
        """Every way pattern p matches given the bound row: yields (row, relationships used)."""
        if p.shortest:
            yield from self.match_shortest(p, row, used)
            return
        n = len(p.nodes)
        start = min(range(n), key=lambda i: self.estimate(p.nodes[i], row))
        for nid in self.candidates(p.nodes[start], row):
            r = dict(row)
            if not self.bind(r, p.nodes[start].var, self.g.nodes[nid]) or not self.early_ok(r, early):
                continue
            slots = [None] * n
            slots[start] = nid
            rels = [None] * len(p.rels)
            yield from self.extend(p, start, start, slots, rels, r, used, early)

    def early_ok(self, row, early) -> bool:
        """WHERE parts that can be checked as soon as a pattern binds their variables, to prune the search early."""
        for c, vs in early:
            if vs <= row.keys() and not _truth(self.ev(c, row)):
                return False
        return True

    def bind(self, row, var, value) -> bool:
        if not var:
            return True
        if var in row:
            cur = row[var]
            return _hashable(cur) == _hashable(value)
        row[var] = value
        return True

    def extend(self, p, lo, hi, slots, rels, row, used, early=()):
        """Grow the matched stretch [lo, hi] of the chain to the right, then to the left."""
        if hi < len(p.nodes) - 1:
            yield from self.step(p, hi, hi + 1, slots, rels, row, used, lo, hi + 1, early)
            return
        if lo > 0:
            yield from self.step(p, lo, lo - 1, slots, rels, row, used, lo - 1, hi, early)
            return
        if p.path_var:
            path = Path([], [])
            for i in range(len(p.nodes)):
                if i == 0:
                    path.nodes.append(slots[0])
                rr = rels[i] if i < len(rels) else None
                if rr is not None:
                    for r, nxt in rr:
                        path.rels.append(r)
                        path.nodes.append(nxt)
            if not self.bind(row, p.path_var, path):
                return
        yield row, used

    def step(self, p, frm, to, slots, rels, row, used, lo, hi, early=()):
        """Match relationship pattern between chain positions frm and to (to = frm +/- 1)."""
        ri = min(frm, to)
        rp = p.rels[ri]
        npat = p.nodes[to]
        # walking right follows the pattern's arrow; walking left goes against it
        direction = rp.direction if to > frm else {"out": "in", "in": "out", "both": "both"}[rp.direction]
        for hops in self.walks(slots[frm], rp, direction, row, used):
            end = hops[-1][1] if hops else slots[frm]
            node = self.g.nodes[end]
            if not self.node_ok(node, npat, row):
                continue
            r2 = dict(row)
            if not self.bind(r2, npat.var, node):
                continue
            seq = hops if to > frm else [(r, n) for (r, n) in _reverse(hops, slots[frm])]
            if rp.var:
                val = [r for r, _ in seq] if rp.varlen else seq[0][0]
                if not self.bind(r2, rp.var, val):
                    continue
            if not self.early_ok(r2, early):
                continue
            s2 = list(slots)
            s2[to] = end
            rels2 = list(rels)
            rels2[ri] = seq
            yield from self.extend(p, lo, hi, s2, rels2, r2, used | {r.id for r, _ in hops}, early)

    def walks(self, start, rp: RelPat, direction, row, used):
        """Sequences of (relationship, node reached) from start: rp.min..rp.max hops, no relationship twice."""
        if not rp.varlen:
            for r, other in self.g.edges(start, direction, rp.types or None):
                self.tick()
                if r.id not in used and self.rel_ok(r, rp, row):
                    yield [(r, other)]
            return
        if rp.min == 0:
            yield []
        stack = [(start, [])]
        while stack:
            at, trail = stack.pop()
            if len(trail) >= rp.max:
                continue
            taken = {r.id for r, _ in trail}
            for r, other in self.g.edges(at, direction, rp.types or None):
                self.tick()
                if r.id in used or r.id in taken or not self.rel_ok(r, rp, row):
                    continue
                t2 = trail + [(r, other)]
                if len(t2) >= rp.min:
                    yield t2
                stack.append((other, t2))

    def match_shortest(self, p: Pattern, row, used):
        a, b = p.nodes
        rp = p.rels[0]
        for sa in self.candidates(a, row):
            ra = dict(row)
            if not self.bind(ra, a.var, self.g.nodes[sa]):
                continue
            targets = set(self.candidates(b, ra))
            if not targets:
                continue
            for end, paths in self.bfs_paths(sa, targets, rp, ra, p.shortest == "all"):
                rb = dict(ra)
                if not self.bind(rb, b.var, self.g.nodes[end]):
                    continue
                for hops in paths:
                    rc = dict(rb)
                    if rp.var and not self.bind(rc, rp.var, [r for r, _ in hops]):
                        continue
                    if p.path_var and not self.bind(rc, p.path_var, Path([sa] + [n for _, n in hops], [r for r, _ in hops])):
                        continue
                    yield rc, used | {r.id for r, _ in hops}

    def bfs_paths(self, start, targets, rp, row, every):
        """For each reachable target, the shortest path (or all of them) from start."""
        dist, parents = {start: 0}, {start: []}
        frontier = deque([start])
        while frontier:
            x = frontier.popleft()
            if dist[x] >= rp.max:
                continue
            for r, y in self.g.edges(x, rp.direction, rp.types or None):
                self.tick()
                if not self.rel_ok(r, rp, row):
                    continue
                if y not in dist:
                    dist[y] = dist[x] + 1
                    parents[y] = [(x, r)]
                    frontier.append(y)
                elif every and dist[y] == dist[x] + 1:
                    parents[y].append((x, r))
        for t in sorted(targets):
            if t == start or t not in dist or dist[t] < rp.min:
                continue
            yield t, self.unwind_paths(t, parents, every)

    def unwind_paths(self, t, parents, every):
        out = []

        def back(node, acc):
            self.tick()
            if not parents[node]:
                out.append(list(reversed(acc)))
                return
            for prev, r in parents[node] if every else parents[node][:1]:
                back(prev, acc + [(r, node)])
                if len(out) >= 100:
                    return

        back(t, [])
        return out

    # projections
    def project(self, p: Projection, rows):
        items = list(p.items)
        if p.star:
            names = sorted({k for r in rows for k in r if not k.startswith("\0")}) if rows else []
            items = [Item(("var", n), n) for n in names] + items
        aliases = [it.alias for it in items]
        if len(set(aliases)) != len(aliases):
            raise CypherError("two columns have the same name; use AS to tell them apart")
        aggregating = any(has_agg(it.expr) for it in items)
        if aggregating:
            out = self.aggregate(items, rows)
            env = out
        else:
            out, env = [], []
            for r in rows:
                self.tick()
                vals = {it.alias: self.ev(it.expr, r) for it in items}
                out.append(vals)
                env.append({**r, **vals})
        if p.distinct:
            seen, keep_out, keep_env = set(), [], []
            for o, e in zip(out, env):
                k = _hashable([o[a] for a in aliases])
                if k not in seen:
                    seen.add(k)
                    keep_out.append(o)
                    keep_env.append(e)
            out, env = keep_out, keep_env
        if p.where is not None:
            pairs = [(o, e) for o, e in zip(out, env) if _truth(self.ev(p.where, {**e, **o}))]
            out, env = [o for o, _ in pairs], [e for _, e in pairs]
        if p.order:
            idx = list(range(len(out)))
            for expr, desc in reversed(p.order):
                keys = [_cmp_key(self.ev(self.alias_expr(expr, items), {**env[i], **out[i]})) for i in idx]
                order = sorted(range(len(idx)), key=lambda j: keys[j], reverse=desc)
                idx = [idx[j] for j in order]
            out = [out[i] for i in idx]
        skip = self.count_arg(p.skip, "SKIP") if p.skip is not None else 0
        limit = self.count_arg(p.limit, "LIMIT") if p.limit is not None else None
        out = out[skip:]
        if limit is not None:
            out = out[:limit]
        return out, aliases

    def alias_expr(self, expr, items):
        # ORDER BY count(x) after RETURN count(x) AS n: sort by the column it names
        if has_agg(expr):
            for it in items:
                if it.expr == expr:
                    return ("var", it.alias)
            raise CypherError("ORDER BY can only use an aggregate that is also returned")
        return expr

    def count_arg(self, e, what):
        v = self.ev(e, {})
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise CypherError(f"{what} needs a whole number of at least 0")
        return v

    def aggregate(self, items, rows):
        keys = [it for it in items if not has_agg(it.expr)]
        aggs = [it for it in items if has_agg(it.expr)]
        groups: dict[Any, tuple[dict, list]] = {}
        for r in rows:
            self.tick()
            kv = {it.alias: self.ev(it.expr, r) for it in keys}
            k = _hashable([kv[it.alias] for it in keys])
            if k not in groups:
                groups[k] = (kv, [])
            groups[k][1].append(r)
        if not groups and not keys:
            groups[()] = ({}, [])
        out = []
        for kv, members in groups.values():
            res = dict(kv)
            for it in aggs:
                calls = []
                _collect_aggs(it.expr, calls)
                vals = {id(c): self.agg_value(c, members) for c in calls}
                base = members[0] if members else {}
                res[it.alias] = self.ev(it.expr, {**base, **kv, "\0agg": vals})
            out.append(res)
        return out

    def agg_value(self, c, members):
        _, name, arg, distinct = c
        if arg is None:
            return len(members)
        vals = [self.ev(arg, m) for m in members]
        vals = [v for v in vals if v is not None]
        if distinct:
            seen, uniq = set(), []
            for v in vals:
                h = _hashable(v)
                if h not in seen:
                    seen.add(h)
                    uniq.append(v)
            vals = uniq
        if name == "count":
            return len(vals)
        if name == "collect":
            return vals
        if not vals:
            return None
        if name in ("sum", "avg"):
            nums = [_num(v, name) for v in vals]
            return sum(nums) if name == "sum" else sum(nums) / len(nums)
        key = min if name == "min" else max
        return key(vals, key=_cmp_key)

    def run(self, q: Query):
        rows = [{}]
        for part in q.parts:
            if isinstance(part, Match):
                rows = self.run_match(part, rows)
            elif isinstance(part, Unwind):
                nxt = []
                for r in rows:
                    v = self.ev(part.expr, r)
                    for x in v if isinstance(v, list) else ([] if v is None else [v]):
                        self.tick()
                        nxt.append({**r, part.var: x})
                rows = nxt
            else:
                out, cols = self.project(part, rows)
                if part.final:
                    return out, cols
                rows = out
        raise CypherError("a query ends with RETURN")


def _collect_aggs(e, acc):
    if isinstance(e, tuple):
        if e[0] == "agg":
            acc.append(e)
            return
        for x in e[1:]:
            if isinstance(x, (tuple, list, dict)):
                _collect_aggs(x, acc)
    elif isinstance(e, list):
        for x in e:
            _collect_aggs(x, acc)
    elif isinstance(e, dict):
        for x in e.values():
            _collect_aggs(x, acc)


def _reverse(hops, start):
    """Hops walked leftwards from `start`, turned to read left to right: [(rel, node after it)]."""
    if not hops:
        return []
    nodes = [start] + [n for _, n in hops]
    rels = [r for r, _ in hops]
    nodes.reverse()
    rels.reverse()
    return [(r, nodes[i + 1]) for i, r in enumerate(rels)]


# ---------- the API ----------
def parse(src: str):
    if not isinstance(src, str) or not src.strip():
        raise CypherError("the query is empty")
    if len(src) > 20_000:
        raise CypherError("the query is too long")
    return Parser(src).parse()


_NESTED = re.compile(r"\((?:[^()\\]|\\.)*[+*}|](?:[^()\\]|\\.)*\)\s*[+*{]")


def run(g: Graph, src: str, params=None, max_rows=DEFAULT_ROWS, max_seconds=MAX_SECONDS):
    """Run a query: {columns, rows (JSON values), nodes and edges it returned (for drawing), truncated, steps, ms}."""
    try:
        return _run(g, src, params, max_rows, max_seconds)
    except RecursionError:
        raise CypherError("the query nests too deeply") from None
    except (OverflowError, MemoryError):
        raise TooBig("a value in the query grew too large") from None
    except (TypeError, ValueError, KeyError, IndexError, AttributeError) as e:
        if isinstance(e, CypherError):
            raise
        raise CypherError(f"can't run that: {type(e).__name__}: {e}") from None


def _run(g, src, params, max_rows, max_seconds):
    started = time.monotonic()
    queries = parse(src)
    runner = Runner(g, params, max_rows=max_rows, max_seconds=max_seconds)
    columns, rows = None, []
    for q, union_all in queries:
        out, cols = runner.run(q)
        if columns is None:
            columns = cols
        elif cols != columns:
            raise CypherError("every part of a UNION returns the same columns")
        rows.extend(out)
        if len(queries) > 1 and not union_all:
            seen, uniq = set(), []
            for r in rows:
                h = _hashable([r[c] for c in columns])
                if h not in seen:
                    seen.add(h)
                    uniq.append(r)
            rows = uniq
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    nodes, edges = {}, {}
    table = [[_json(r[c], g, nodes, edges) for c in columns] for r in rows]
    for e in list(edges.values()):
        for end in (e["a"], e["b"]):
            if end not in nodes and end in g.nodes:
                n = g.nodes[end]
                nodes[end] = {"id": n.id, "labels": list(n.labels), **n.props}
    return {
        "columns": columns,
        "rows": table,
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "truncated": truncated,
        "steps": runner.steps,
        "ms": round((time.monotonic() - started) * 1000, 1),
    }


def _json(v, g, nodes, edges):
    """A value as JSON; nodes and relationships also go into `nodes` and `edges` for drawing."""
    from .graph_model import rel_out

    if isinstance(v, Node):
        nodes[v.id] = {"id": v.id, "labels": list(v.labels), **v.props}
        return {"id": v.id, "labels": list(v.labels), **v.props}
    if isinstance(v, Rel):
        edges[v.id] = rel_out(v)
        return rel_out(v)
    if isinstance(v, Path):
        for n in v.nodes:
            _json(g.nodes[n], g, nodes, edges)
        for r in v.rels:
            _json(r, g, nodes, edges)
        return {"nodes": v.nodes, "edges": [r.id for r in v.rels], "length": len(v.rels)}
    if isinstance(v, list):
        return [_json(x, g, nodes, edges) for x in v]
    if isinstance(v, dict):
        return {k: _json(x, g, nodes, edges) for k, x in v.items()}
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v
