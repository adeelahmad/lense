"""Full-text search in a small SQLite file next to the database (search.engine "sqlite", the default).

SurrealDB keeps the records and the graph. Its own full-text index cost ten times the records' memory and disk
(10,000 transcript lines: 535 MB of RAM and 100 MB on disk with it, 51 MB and 6 MB without), so the words are indexed
here instead: SQLite's FTS5, with BM25 ranking, stemming and highlights, in a few MB, in the Python standard library.

Keeping it in step needs nothing from the code that writes text: each table's event (`event`) notes the rows that
were created, deleted or whose text changed in `text_change`, and the index catches up from there before each search.
The first search with an empty index builds it from the tables, and so does the first search after the database was
swapped for another (a restore, or a new one under the same name). Who may see a row is still decided by SurrealDB:
the index only says which rows have the words. search.engine "surrealdb" keeps SurrealDB's own index instead.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
import sqlite3
import threading
import time
import uuid

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


def _quote(term):
    return '"' + term.replace('"', '""') + '"'


def match_expr(groups):
    """An FTS5 query for parsed search groups (search.parse_query): each group's words and phrases all present, any
    group will do."""
    parts = []
    for g in groups:
        terms = [_quote(t) for t in g["words"] + g["phrases"] if t.strip()]
        if terms:
            parts.append("(" + " ".join(terms) + ")")
    return " OR ".join(parts)


def words_expr(text):
    """An FTS5 query that needs every word of plain text."""
    return " ".join(_quote(w) for w in re.findall(r"[\w'’.-]+", text or "") if w.strip(".-'’"))


class Index:
    """One SQLite file per database: an FTS5 table for each text table, and the row ids they map to."""

    def __init__(self, db, cfg):
        self.db = db
        self.tokenize = (
            "porter unicode61 remove_diacritics 2" if cfg["search"].get("stemming") == "english" else "unicode61 remove_diacritics 2"
        )
        self.lock = threading.Lock()
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
            self._drop_tables()
        self._tables()
        self.con.commit()

    def _tables(self):
        for t in TABLES:
            self.con.execute(f"CREATE TABLE IF NOT EXISTS ids_{t} (rowid INTEGER PRIMARY KEY, rid TEXT UNIQUE NOT NULL)")
            self.con.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS fts_{t} USING fts5(text, tokenize = '{self.tokenize}')")

    def _drop_tables(self):
        for t in TABLES:
            self.con.execute(f"DROP TABLE IF EXISTS ids_{t}")
            self.con.execute(f"DROP TABLE IF EXISTS fts_{t}")
        self.con.execute("DELETE FROM meta")

    def _meta(self, key):
        row = self.con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _token(self):
        """This database's mark: an index built from another database (a restore, a new one) isn't trusted."""
        return self.db.values("SELECT VALUE token FROM $r", r=store.R("text_change_state", "index"))

    def close(self):
        with self.lock:
            self.con.close()

    def rebuild(self):
        """Index everything again (`lens reindex`, Settings → Search → Reindex)."""
        with self.lock:
            self._drop_tables()
            self._tables()
            self.con.commit()
        self.sync()

    # ---------- keeping in step ----------
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

    def build(self):
        """Index every row of every text table (a new index, or after `lens reindex`)."""
        t0 = time.time()
        self.db.q("DELETE text_change")  # everything is read from the tables now
        token = uuid.uuid4().hex
        self.db.q("UPSERT $r SET token = $t", r=store.R("text_change_state", "index"), t=token)
        for t in TABLES:
            self.con.execute(f"DELETE FROM ids_{t}")
            self.con.execute(f"DELETE FROM fts_{t}")
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
        self.con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('tokenize', ?)", (self.tokenize,))
        self.con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('built', ?)", (token,))
        self.con.commit()
        if n:
            log.info("indexed %d lines for full-text search in %.1fs", n, time.time() - t0)

    def sync(self):
        """Catch up with what changed since the last search (text_change), building the index the first time."""
        with self.lock:
            built = self._meta("built")
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
                self.con.commit()
                self.db.q("DELETE text_change WHERE id IN $ids", ids=[store.R("text_change", c["id"]) for c in changes])
                if len(changes) < BATCH:
                    break

    # ---------- searching ----------
    def find(self, table, expr, limit, marks=None):
        """[(id, score, marked text or None)] of `table`'s rows matching the FTS5 query `expr`, best first. Scores are
        BM25, higher is better; `marks` (start, end) wraps the matched words."""
        if not expr:
            return []
        self.sync()
        hl = f"highlight(fts_{table}, 0, ?, ?)" if marks else "NULL"
        args = ([*marks] if marks else []) + [expr, int(limit)]
        with self.lock:
            try:
                rows = self.con.execute(
                    f"SELECT i.rid, -bm25(fts_{table}), {hl} FROM fts_{table} JOIN ids_{table} AS i ON i.rowid = fts_{table}.rowid "
                    f"WHERE fts_{table} MATCH ? ORDER BY bm25(fts_{table}) LIMIT ?",
                    args,
                ).fetchall()
            except sqlite3.OperationalError as e:  # a query FTS5 can't parse: nothing matches it
                log.info("full-text query %r: %s", expr, e)
                return []
        return [(json.loads(rid), score, marked) for rid, score, marked in rows]


def rows(db, table, expr, fields, where="", params=None, limit=50, marks=None):
    """The rows of `table` (`fields`, narrowed by `where`, which starts with " AND ", and its `params`) whose text
    matches the FTS5 query `expr`, best first, each with `s1` (its BM25 score) and, with `marks`, `h1` (its text with
    the matched words marked). None when this database has no SQLite index (search.engine isn't sqlite)."""
    ix = getattr(db, "textindex", None)
    if ix is None:
        return None
    want = limit
    while True:
        # the words first, then SurrealDB keeps the rows `where` allows: ask for more while it turns too many away
        found = ix.find(table, expr, want * 4 if where else want, marks)
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
