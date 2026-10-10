"""SurrealDB asks for some transactions to be run again (a write conflict, a snapshot older than the commit oracle's
GC window on a busy server); the store runs them again instead of failing."""

from __future__ import annotations

import pytest

from app.domain import store

GC = "There was a problem with a transaction: Transaction retry required: snapshot is older than the commit oracle's GC window"


class _Conn:
    """A stand-in server connection: its first `failures` answers are SurrealDB's retry error, then OK."""

    socket = None

    def __init__(self, failures):
        self.failures, self.calls = failures, 0

    def query_raw(self, sql, v):
        self.calls += 1
        if self.calls <= self.failures:
            return {"result": [{"status": "ERR", "result": GC}]}
        return {"result": [{"status": "OK", "result": []}]}

    def query(self, sql, v):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(GC)
        return [1]


def _db(conn):
    d = store.DB.__new__(store.DB)
    d.url, d.embedded = "ws://test", False
    d._pool = store.queue.LifoQueue()
    d._all = [conn]
    d._pool.put(conn)
    return d


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(store, "_backoff", lambda attempt: None)


def test_a_snapshot_older_than_the_gc_window_is_retried():
    assert store._retryable(GC)
    c = _Conn(failures=2)
    _db(c).run(["DEFINE INDEX IF NOT EXISTS field_space ON field FIELDS space"], transaction=False)
    assert c.calls == 3
    c = _Conn(failures=1)
    assert _db(c).values("SELECT 1") == [1]
    assert c.calls == 2


def test_other_errors_still_fail_at_once():
    class _Bad(_Conn):
        def query_raw(self, sql, v):
            self.calls += 1
            return {"result": [{"status": "ERR", "result": "Parse error: unexpected token"}]}

    c = _Bad(failures=0)
    with pytest.raises(RuntimeError, match="Parse error"):
        _db(c).run(["SELEC 1"], transaction=False)
    assert c.calls == 1
