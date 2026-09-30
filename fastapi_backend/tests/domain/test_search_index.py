"""The embedded engine's full-text index must survive closing and reopening the database.

SurrealDB 2.x inside the Python SDK drops postings from its SEARCH index on reopen, so after a restart most segments
silently disappear from search. `DB.ready_fulltext()` rebuilds the index once per process; these tests pin that down.
"""

from __future__ import annotations

from app.domain import ingest, search, store
from tests.conftest import make_cfg
from tests.helpers import PODS1, PODS2, quiet

# word -> segments containing it across PODS1 and PODS2
EXPECTED = {"capsid": 4, "gene": 1, "published": 1, "exploit": 3, "frightening": 1, "safety": 1}


def _counts(db):
    return {w: search.search(db, w)["total"] for w in EXPECTED}


def test_search_finds_every_segment_after_reopen(tmp_path):
    # Always a file-backed embedded database, whatever LENS_TEST_SURREAL_URL says: that's where the bug lives.
    cfg = make_cfg(tmp_path, url="surrealkv://" + str(tmp_path / "kv"))
    db = store.connect(cfg)
    for name, text in (("ep1.txt", PODS1), ("ep2.txt", PODS2)):
        (tmp_path / name).write_text(text)
        ingest.import_transcript(db, cfg, "pods", tmp_path / name, log=quiet)
    assert _counts(db) == EXPECTED
    db.close()

    db = store.connect(cfg)  # a new process would do exactly this
    try:
        assert _counts(db) == EXPECTED
        # and writes after the repair are searchable too (two short lines import as one paragraph segment)
        (tmp_path / "ep3.txt").write_text("Alice: The zeppelin landed at dawn.\nBob: A zeppelin, really?")
        ingest.import_transcript(db, cfg, "pods", tmp_path / "ep3.txt", log=quiet)
        assert search.search(db, "zeppelin")["total"] == 1
    finally:
        db.close()


def test_repair_runs_once_per_process(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, url="surrealkv://" + str(tmp_path / "kv"))
    db = store.connect(cfg)
    try:
        rebuilds = []
        real_q = db.q
        monkeypatch.setattr(db, "q", lambda sql, **v: (rebuilds.append(sql) if sql.startswith("REBUILD") else None) or real_q(sql, **v))
        for _ in range(3):
            search.search(db, "anything")
        assert len([s for s in rebuilds if "segment_text" in s]) == 1
        store.reindex(db, cfg)  # a fresh index needs no repair
        rebuilds.clear()
        search.search(db, "anything")
        assert rebuilds == []
    finally:
        db.close()
