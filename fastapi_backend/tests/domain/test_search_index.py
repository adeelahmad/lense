"""Full-text search must find every line after the database is closed and reopened, with either engine.

search.engine "sqlite" (the default) keeps the words in a SQLite file kept in step by SurrealDB events (textindex.py).
With "surrealdb", SurrealDB 2.x inside the Python SDK drops postings from its SEARCH index on reopen, so after a restart
most segments would silently disappear from search: `DB.ready_fulltext()` rebuilds the index once per process.
"""

from __future__ import annotations

import os

import pytest

from app.domain import chat, ingest, search, store, textindex
from tests.conftest import make_cfg
from tests.helpers import PODS1, PODS2, quiet

# word -> segments containing it across PODS1 and PODS2
EXPECTED = {"capsid": 4, "gene": 1, "published": 1, "exploit": 3, "frightening": 1, "safety": 1}


# the word indexes kept outside SurrealDB; OpenSearch only with LENS_TEST_OPENSEARCH_URL (a real cluster to test against)
OPENSEARCH = os.environ.get("LENS_TEST_OPENSEARCH_URL")
OUTSIDE = [
    "sqlite",
    pytest.param("opensearch", marks=pytest.mark.skipif(not OPENSEARCH, reason="LENS_TEST_OPENSEARCH_URL: an OpenSearch to test against")),
]


def _engine(name):
    return {"engine": name, "opensearch_url": OPENSEARCH or ""}


def _counts(db):
    return {w: search.search(db, w)["total"] for w in EXPECTED}


@pytest.mark.parametrize("engine", [*OUTSIDE, "surrealdb"])
def test_search_finds_every_segment_after_reopen(tmp_path, engine):
    # Always a file-backed embedded database, whatever LENS_TEST_SURREAL_URL says: that's where the bug lives.
    cfg = make_cfg(tmp_path, url="surrealkv://" + str(tmp_path / "kv"), search=_engine(engine))
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
    cfg = make_cfg(tmp_path, url="surrealkv://" + str(tmp_path / "kv"), search={"engine": "surrealdb"})
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


def _imported(tmp_path, **overrides):
    cfg = make_cfg(tmp_path, **overrides)
    db = store.connect(cfg)
    for name, text in (("ep1.txt", PODS1), ("ep2.txt", PODS2)):
        (tmp_path / name).write_text(text)
        ingest.import_transcript(db, cfg, "pods", tmp_path / name, log=quiet)
    return cfg, db


def _indexes(db):
    return {i for _, table in store.TEXT_INDEXES for i in (db.one(f"INFO FOR TABLE {table}") or {}).get("indexes", {})}


def test_sqlite_is_the_default_and_surrealdb_keeps_no_text_index(tmp_path):
    cfg, db = _imported(tmp_path)
    try:
        assert db.textindex is not None and db.fulltext is None
        assert not _indexes(db) & {index for index, _ in store.TEXT_INDEXES}
        assert _counts(db) == EXPECTED
        hit = search.search(db, "capsid")["hits"][0]
        assert "<mark>" in hit["snippet"].lower()
        # the assistant's retrieval and IIIF search read the same index
        assert chat.retrieve(db, "capsid", {store.ns_id(db, "pods")})
    finally:
        db.close()


@pytest.mark.parametrize("engine", OUTSIDE)
def test_edits_and_deletes_reach_the_index(tmp_path, engine):
    cfg, db = _imported(tmp_path, search=_engine(engine))
    try:
        assert search.search(db, "zeppelin")["total"] == 0
        sid = db.values("SELECT VALUE record::id(id) FROM segment WHERE text CONTAINS 'capsid' LIMIT 1")[0]
        db.q("UPDATE $r SET text = 'A zeppelin over the lab'", r=store.R("segment", sid))
        assert search.search(db, "zeppelin")["total"] == 1
        assert search.search(db, "capsid")["total"] == EXPECTED["capsid"] - 1
        db.q("DELETE $r", r=store.R("segment", sid))
        assert search.search(db, "zeppelin")["total"] == 0
        assert db.rows("SELECT * FROM text_change") == []
    finally:
        db.close()


def test_switching_engines_with_reindex(tmp_path):
    from app.domain import settings

    cfg, db = _imported(tmp_path)
    try:
        settings.save(db, cfg, "search", {"engine": "surrealdb"})
        store.reindex(db, cfg)
        assert db.textindex is None and db.fulltext
        assert {index for index, _ in store.TEXT_INDEXES} <= _indexes(db)
        assert _counts(db) == EXPECTED
        settings.save(db, cfg, "search", {"engine": "sqlite"})
        store.reindex(db, cfg)
        assert db.textindex is not None and not _indexes(db) & {index for index, _ in store.TEXT_INDEXES}
        assert _counts(db) == EXPECTED
    finally:
        db.close()


@pytest.mark.parametrize("engine", OUTSIDE)
def test_an_index_from_another_database_is_rebuilt(tmp_path, engine):
    # a restored or new database under the same name: the old file's lines must not be found in it
    url = "surrealkv://" + str(tmp_path / "kv")
    cfg, db = _imported(tmp_path, url=url, search=_engine(engine))
    assert search.search(db, "capsid")["total"] == EXPECTED["capsid"]
    db.close()
    import shutil

    shutil.rmtree(tmp_path / "kv")
    db = store.connect(cfg)
    try:
        # lines with the old ids that arrived without the events (as from a restore): an index trusted blindly
        # would find "capsid" in them
        db.q("REMOVE EVENT segment_textindex ON segment")
        db.q("FOR $i IN 1..40 { CREATE type::thing('segment', $i) SET text = 'a zeppelin' }")
        db.q(textindex.event("segment"))
        expr = textindex.words
        assert textindex.rows(db, "segment", expr("capsid"), "text") == []
        assert len(textindex.rows(db, "segment", expr("zeppelin"), "text")) == len(db.rows("SELECT id FROM segment"))
    finally:
        db.close()


@pytest.mark.parametrize("engine", OUTSIDE)
def test_filters_narrow_after_the_words(tmp_path, engine):
    cfg, db = _imported(tmp_path, search=_engine(engine))
    try:
        other = store.ns_id(db, "calls")
        assert search.search(db, "capsid", spaces={other})["total"] == 0
        assert search.search(db, "capsid", ns="pods")["total"] == EXPECTED["capsid"]
        assert textindex.rows(db, "segment", search.parse_query("capsid"), "text", " AND space = $s", {"s": other}) == []
    finally:
        db.close()


@pytest.mark.skipif(not OPENSEARCH, reason="LENS_TEST_OPENSEARCH_URL: an OpenSearch to test against")
def test_opensearch_finds_marks_and_ranks(tmp_path, caplog):
    cfg, db = _imported(tmp_path, search=_engine("opensearch"))
    try:
        assert type(db.textindex).__name__ == "OpenSearch"
        assert _counts(db) == EXPECTED
        hit = search.search(db, "capsid")["hits"][0]
        assert "<mark>" in hit["snippet"].lower()
        assert chat.retrieve(db, "capsid", {store.ns_id(db, "pods")})
        assert "unavailable" not in caplog.text  # found by OpenSearch, not by reading the tables
    finally:
        db.close()


def test_an_unreachable_opensearch_falls_back_to_reading_the_tables(tmp_path):
    cfg, db = _imported(tmp_path, search={"engine": "opensearch", "opensearch_url": "http://127.0.0.1:9"})
    try:
        assert type(db.textindex).__name__ == "OpenSearch"
        assert _counts(db) == EXPECTED
    finally:
        db.close()


def test_opensearch_needs_an_address(tmp_path):
    from app.domain import settings

    cfg, db = _imported(tmp_path)
    try:
        with pytest.raises(ValueError, match="opensearch_url"):
            settings.save(db, cfg, "search", {"engine": "opensearch"})
        settings.save(db, cfg, "search", {"engine": "opensearch", "opensearch_url": "http://search.lan:9200", "opensearch_password": "pw"})
        saved = settings.effective(db, cfg)["search"]
        assert saved["opensearch_password"] == "pw" and saved["engine"] == "opensearch"
    finally:
        db.close()
