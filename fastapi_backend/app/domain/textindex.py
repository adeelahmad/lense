"""Word search outside SurrealDB: in a small SQLite file next to the database (search.engine "sqlite", the default) or
in an OpenSearch cluster ("opensearch").

SurrealDB keeps the records and the graph. Its own full-text index cost about six times the records' memory (10,000
transcript lines: 380 MB of server memory with it, 64 MB without), so the words are indexed elsewhere: SQLite's FTS5,
with BM25 ranking, stemming and highlights, in a few MB and in the Python standard library; or, for archives too big
for one small machine, OpenSearch on another node.

Keeping it in step needs nothing from the code that writes text: each table's event (`event`) notes the rows that
were created, deleted or whose text changed in `text_change`, and the index catches up from there before each search.
The first search with an empty index builds it from the tables, and so does the first search after the database was
swapped for another (a restore, or a new one under the same name). Who may see a row is still decided by SurrealDB:
the index only says which rows have the words. search.engine "surrealdb" keeps SurrealDB's own index instead.
"""

from __future__ import annotations

import base64
import json
import logging
import pathlib
import re
import sqlite3
import threading
import time
import urllib.parse
import uuid

import httpx

from . import store

log = logging.getLogger("lens")

TABLES = tuple(table for _, table in store.TEXT_INDEXES)
BATCH = 2000
MAX = 20000  # the most matches looked at for one search


def event(table):
    """The SurrealDB event that notes a row of `table` the index has to look at again."""
    return (
        f"DEFINE EVENT IF NOT EXISTS {table}_textindex ON {table} "
        "WHEN $event = 'CREATE' OR $event = 'DELETE' OR $before.text != $after.text "
        f"THEN (CREATE text_change SET tb = '{table}', rid = record::id($value.id))"
    )


def words(text):
    """Search groups (as search.parse_query makes them) that need every word of plain text."""
    ws = [w for w in re.findall(r"[\w'’.-]+", text or "") if w.strip(".-'’")]
    return [{"words": ws, "phrases": []}] if ws else []


def open_index(db, cfg):
    """The word index search.engine names (cfg["search"]), or None for SurrealDB's own."""
    engine = cfg["search"].get("engine")
    if engine == "opensearch":
        if cfg["search"].get("opensearch_url"):
            return OpenSearch(db, cfg)
        log.warning("search.engine is opensearch but search.opensearch_url is empty: using the built-in index")
    return None if engine == "surrealdb" else SQLite(db, cfg)


class Unavailable(Exception):
    """The index can't be reached (OpenSearch down): search falls back to reading the tables."""


class Engine:
    """What every word index shares: keeping in step with SurrealDB through `text_change`, and building itself from the
    tables when it is new or was built from another database. Subclasses store and search the words."""

    stemming = "english"

    def __init__(self, db, cfg):
        self.db = db
        self.stemming = cfg["search"].get("stemming") or "english"
        self.lock = threading.Lock()

    # what a subclass provides
    def _built(self):  # the token of the database it was built from, or None
        raise NotImplementedError

    def _reset(self):  # empty, ready to build
        raise NotImplementedError

    def _put(self, table, rows):
        raise NotImplementedError

    def _drop(self, table, rids):
        raise NotImplementedError

    def _done(self, token=None):  # make the writes so far visible, and with a token, mark the index built
        raise NotImplementedError

    def _find(self, table, groups, limit, marks):
        raise NotImplementedError

    def close(self):
        pass

    def _token(self):
        """This database's mark: an index built from another database (a restore, a new one) isn't trusted."""
        return self.db.values("SELECT VALUE token FROM $r", r=store.R("text_change_state", "index"))

    def rebuild(self):
        """Index everything again (`lens reindex`, Settings → Search → Reindex)."""
        with self.lock:
            self.build()

    def build(self):
        """Index every row of every text table (a new index, or after `lens reindex`)."""
        t0 = time.time()
        self.db.q("DELETE text_change")  # everything is read from the tables now
        token = uuid.uuid4().hex
        self.db.q("UPSERT $r SET token = $t", r=store.R("text_change_state", "index"), t=token)
        self._reset()
        n = 0
        for t in TABLES:
            last = None
            while True:
                rows = self.db.rows(
                    f"SELECT record::id(id) AS id, text FROM {t}"
                    + (" WHERE id > $last" if last is not None else "")
                    + f" ORDER BY id LIMIT {BATCH}",
                    last=last,
                )
                if not rows:
                    break
                self._put(t, rows)
                n += len(rows)
                last = store.R(t, rows[-1]["id"])
                if len(rows) < BATCH:
                    break
        self._done(token)
        if n:
            log.info("indexed %d lines for word search in %.1fs", n, time.time() - t0)

    def sync(self):
        """Catch up with what changed since the last search (text_change), building the index the first time."""
        with self.lock:
            built = self._built()
            if built is None or [built] != self._token():
                self.build()
            while True:
                changes = self.db.rows(f"SELECT record::id(id) AS id, tb, rid FROM text_change LIMIT {BATCH}")
                if not changes:
                    break
                by_table = {}
                for c in changes:
                    if c.get("tb") in TABLES and c.get("rid") is not None:
                        by_table.setdefault(c["tb"], set()).add(json.dumps(c["rid"]))
                for t, rids in by_table.items():
                    keys = [json.loads(r) for r in rids]
                    rows = self.db.rows(f"SELECT record::id(id) AS id, text FROM {t} WHERE id IN $ids", ids=[store.R(t, k) for k in keys])
                    self._put(t, rows)
                    present = {json.dumps(r["id"]) for r in rows}
                    self._drop(t, [json.loads(x) for x in rids - present])
                self._done()
                self.db.q("DELETE text_change WHERE id IN $ids", ids=[store.R("text_change", c["id"]) for c in changes])
                if len(changes) < BATCH:
                    break

    def find(self, table, groups, limit, marks=None):
        """[(id, score, marked text or None)] of `table`'s rows matching the search groups, best first. Scores are
        BM25, higher is better; `marks` (start, end) wraps the matched words."""
        if not any(g["words"] or g["phrases"] for g in groups):
            return []
        self.sync()
        return self._find(table, groups, int(limit), marks)


def _quote(term):
    return '"' + term.replace('"', '""') + '"'


def match_expr(groups):
    """An FTS5 query for search groups: each group's words and phrases all present, any group will do."""
    parts = []
    for g in groups:
        terms = [_quote(t) for t in g["words"] + g["phrases"] if t.strip()]
        if terms:
            parts.append("(" + " ".join(terms) + ")")
    return " OR ".join(parts)


class SQLite(Engine):
    """One SQLite file per database: an FTS5 table for each text table, and the row ids they map to."""

    def __init__(self, db, cfg):
        super().__init__(db, cfg)
        self.tokenize = "porter unicode61 remove_diacritics 2" if self.stemming == "english" else "unicode61 remove_diacritics 2"
        if db.url.split(":", 1)[0] in ("mem", "memory"):  # a database in memory gets its index in memory too
            self.path = ":memory:"
        else:
            ns, name = db._target
            folder = pathlib.Path(cfg["data_dir"]) / "search"
            folder.mkdir(parents=True, exist_ok=True)
            self.path = str(folder / f"{re.sub(r'[^\w.-]', '_', ns)}-{re.sub(r'[^\w.-]', '_', name)}.sqlite")
        self.con = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        if self._meta("tokenize") not in (None, self.tokenize):  # stemming changed: the words are indexed again
            self._reset()
        self._tables()
        self.con.commit()

    def _tables(self):
        for t in TABLES:
            self.con.execute(f"CREATE TABLE IF NOT EXISTS ids_{t} (rowid INTEGER PRIMARY KEY, rid TEXT UNIQUE NOT NULL)")
            self.con.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS fts_{t} USING fts5(text, tokenize = '{self.tokenize}')")

    def _meta(self, key):
        row = self.con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _built(self):
        return self._meta("built")

    def _reset(self):
        for t in TABLES:
            self.con.execute(f"DROP TABLE IF EXISTS ids_{t}")
            self.con.execute(f"DROP TABLE IF EXISTS fts_{t}")
        self.con.execute("DELETE FROM meta")
        self._tables()

    def close(self):
        with self.lock:
            self.con.close()

    def _put(self, table, rows):
        cur = self.con.cursor()
        for r in rows:
            rid = json.dumps(r["id"])
            cur.execute(f"INSERT OR IGNORE INTO ids_{table} (rid) VALUES (?)", (rid,))
            rowid = cur.execute(f"SELECT rowid FROM ids_{table} WHERE rid = ?", (rid,)).fetchone()[0]
            cur.execute(f"DELETE FROM fts_{table} WHERE rowid = ?", (rowid,))
            cur.execute(f"INSERT INTO fts_{table} (rowid, text) VALUES (?, ?)", (rowid, r.get("text") or ""))

    def _drop(self, table, rids):
        cur = self.con.cursor()
        for rid in rids:
            row = cur.execute(f"SELECT rowid FROM ids_{table} WHERE rid = ?", (json.dumps(rid),)).fetchone()
            if row:
                cur.execute(f"DELETE FROM fts_{table} WHERE rowid = ?", (row[0],))
                cur.execute(f"DELETE FROM ids_{table} WHERE rowid = ?", (row[0],))

    def _done(self, token=None):
        if token:
            self.con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('tokenize', ?)", (self.tokenize,))
            self.con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('built', ?)", (token,))
        self.con.commit()

    def _find(self, table, groups, limit, marks):
        expr = match_expr(groups)
        if not expr:
            return []
        hl = f"highlight(fts_{table}, 0, ?, ?)" if marks else "NULL"
        args = ([*marks] if marks else []) + [expr, limit]
        with self.lock:
            try:
                rows = self.con.execute(
                    f"SELECT i.rid, -bm25(fts_{table}), {hl} FROM fts_{table} JOIN ids_{table} AS i ON i.rowid = fts_{table}.rowid "
                    f"WHERE fts_{table} MATCH ? ORDER BY bm25(fts_{table}) LIMIT ?",
                    args,
                ).fetchall()
            except sqlite3.OperationalError as e:  # a query FTS5 can't parse: nothing matches it
                log.info("word search %r: %s", expr, e)
                return []
        return [(json.loads(rid), score, marked) for rid, score, marked in rows]


class OpenSearch(Engine):
    """An OpenSearch (or Elasticsearch-compatible) cluster on another machine: an index per text table, named after
    this database, and a small one saying which database it was built from."""

    def __init__(self, db, cfg):
        super().__init__(db, cfg)
        s = cfg["search"]
        ns, name = db._target
        self.prefix = "lens-" + re.sub(r"[^a-z0-9_-]", "_", f"{ns}-{name}".lower())
        self.analyzer = "english" if self.stemming == "english" else "standard"
        headers = {"Content-Type": "application/json"}
        if s.get("opensearch_user"):
            pair = f"{s['opensearch_user']}:{s.get('opensearch_password') or ''}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
        self.http = httpx.Client(
            base_url=s["opensearch_url"].rstrip("/"), headers=headers, timeout=30, verify=s.get("opensearch_verify", True) is not False
        )

    def _call(self, method, path, body=None, ndjson=None, ok=(200, 201)):
        try:
            if ndjson is not None:
                r = self.http.request(method, path, content=ndjson, headers={"Content-Type": "application/x-ndjson"})
            else:
                r = self.http.request(method, path, json=body)
        except httpx.HTTPError as e:
            raise Unavailable(f"OpenSearch at {self.http.base_url}: {e}") from e
        if r.status_code not in ok:
            raise Unavailable(f"OpenSearch {method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else {}

    def _index(self, table):
        return f"{self.prefix}-{table}"

    def _built(self):
        got = self._call("GET", f"/{self.prefix}-meta/_doc/state", ok=(200, 404))
        src = got.get("_source") or {}
        return src.get("built") if src.get("analyzer") == self.analyzer else None

    def _reset(self):
        for t in [*TABLES, "meta"]:
            self._call("DELETE", f"/{self.prefix}-{t}", ok=(200, 404))
        mapping = {"mappings": {"properties": {"text": {"type": "text", "analyzer": self.analyzer}}}}
        for t in TABLES:
            self._call("PUT", f"/{self._index(t)}", mapping)

    def _bulk(self, lines):
        if not lines:
            return
        got = self._call("POST", "/_bulk", ndjson="\n".join(json.dumps(x) for x in lines) + "\n")
        if got.get("errors"):
            bad = next((i for i in got.get("items", []) for v in i.values() if v.get("error")), None)
            raise Unavailable(f"OpenSearch refused some lines: {json.dumps(bad)[:300]}")

    def _put(self, table, rows):
        lines = []
        for r in rows:
            lines += [{"index": {"_index": self._index(table), "_id": json.dumps(r["id"])}}, {"text": r.get("text") or ""}]
        self._bulk(lines)

    def _drop(self, table, rids):
        self._bulk([{"delete": {"_index": self._index(table), "_id": json.dumps(rid)}} for rid in rids])

    def _done(self, token=None):
        if token:
            self._call("PUT", f"/{self.prefix}-meta/_doc/state", {"built": token, "analyzer": self.analyzer})
        self._call("POST", f"/{self.prefix}-*/_refresh")  # searches see what was just written

    def close(self):
        self.http.close()

    def _find(self, table, groups, limit, marks):
        should = []
        for g in groups:
            must = [{"match": {"text": {"query": " ".join(g["words"]), "operator": "and"}}}] if g["words"] else []
            must += [{"match_phrase": {"text": p}} for p in g["phrases"]]
            if must:
                should.append({"bool": {"must": must}})
        if not should:
            return []
        body = {"size": limit, "_source": False, "query": {"bool": {"should": should, "minimum_should_match": 1}}}
        if marks:
            body["highlight"] = {"pre_tags": [marks[0]], "post_tags": [marks[1]], "fields": {"text": {"number_of_fragments": 0}}}
        got = self._call("POST", f"/{urllib.parse.quote(self._index(table))}/_search", body)
        return [
            (json.loads(h["_id"]), h.get("_score") or 0.0, (h.get("highlight", {}).get("text") or [None])[0])
            for h in got.get("hits", {}).get("hits", [])
        ]


def rows(db, table, groups, fields, where="", params=None, limit=50, marks=None):
    """The rows of `table` (`fields`, narrowed by `where`, which starts with " AND ", and its `params`) whose text
    matches the search groups, best first, each with `s1` (its BM25 score) and, with `marks`, `h1` (its text with the
    matched words marked). None when there is no word index here (search.engine "surrealdb") or it can't be reached;
    callers then use SurrealDB's index or read the table."""
    ix = getattr(db, "textindex", None)
    if ix is None:
        return None
    want = limit
    while True:
        # the words first, then SurrealDB keeps the rows `where` allows: ask for more while it turns too many away
        try:
            found = ix.find(table, groups, want * 4 if where else want, marks)
        except Unavailable as e:
            log.warning("word search is unavailable, reading the tables instead: %s", e)
            return None
        if not found:
            return []
        by_id = {json.dumps(rid): (score, marked) for rid, score, marked in found}
        got = db.rows(
            f"SELECT record::id(id) AS _tx, {fields} FROM {table} WHERE id IN $tx_ids{where}",
            tx_ids=[store.R(table, rid) for rid, _, _ in found],
            **(params or {}),
        )
        if len(got) >= limit or len(found) < (want * 4 if where else want) or want >= MAX:
            break
        want *= 8
    for r in got:
        score, marked = by_id.get(json.dumps(r.pop("_tx")), (0.0, None))
        r["s1"] = score
        if marks:
            r["h1"] = marked if marked is not None else r.get("text")
    got.sort(key=lambda r: -r["s1"])
    return got[:limit]
