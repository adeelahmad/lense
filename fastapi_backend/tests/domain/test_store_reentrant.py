"""A thread that holds a database connection and asks for one again gets the same one, instead of waiting forever for
itself. The garbage collector closes an abandoned streamed answer (its reader navigated away) on whatever thread it
runs on, in the middle of that thread's own query, and the answer's cleanup queries too; with the embedded engine's
single connection that was a self-deadlock that froze the whole server (seen in the clicks survey, PR #400)."""

from __future__ import annotations

import threading
import types

import pytest

from app.domain import store


def _in_time(fn, seconds=15):
    """`fn` run on its own thread, so a deadlock fails the test instead of hanging it; its result."""
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("value", fn()), daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), "the query waited forever for the connection its own thread holds"
    return out["value"]


def test_a_generator_closed_mid_query_can_still_query(db):
    """What the garbage collector does to a left-behind streamed answer: close it, on a thread that is itself inside a
    query, so its cleanup's query runs while that thread holds the connection."""
    if not db.embedded:
        pytest.skip("a server gets a pool: a second connection answers")
    seen = {}

    def answer():
        try:
            yield "token"
        finally:
            seen["cleanup"] = db.values("RETURN 2")  # on.end() in a streamed chat answer

    def run():
        g = answer()
        next(g)
        with db.conn():  # this thread is mid-query (the connection is taken) when the collector closes the generator
            g.close()
        return db.values("RETURN 3")

    assert _in_time(run) == [3] and seen["cleanup"] == [2]


def test_the_connection_comes_back_to_the_pool_afterwards(db):
    if not db.embedded:
        pytest.skip("a server gets a pool: a second connection answers")

    def run():
        with db.conn():
            inner = db.values("RETURN 1")
        return inner, db.values("RETURN 4")

    assert _in_time(run) == ([1], [4])
    # another thread is not given this thread's connection: it takes it from the pool, after it is back
    assert _in_time(lambda: db.values("RETURN 5")) == [5]


def test_against_a_server_a_nested_query_takes_another_connection():
    """The SDK sends on a server connection under a lock the same thread cannot take twice, so sharing the held one
    would hang there too; a server has a pool, and the inner query takes a second connection from it."""
    d = store.DB.__new__(store.DB)
    d.url, d.embedded, d._size = "ws://test", False, 2
    d._pool, d._held, d._grow_lock = store.queue.LifoQueue(), store.threading.local(), store.threading.Lock()
    first, second = types.SimpleNamespace(socket=None), types.SimpleNamespace(socket=None)
    d._all = [first, second]
    d._pool.put(second)
    d._pool.put(first)
    with d.conn() as outer, d.conn() as inner:
        assert outer is first and inner is second
        assert d._held.c is inner
    assert d._held.c is None and d._pool.qsize() == 2
