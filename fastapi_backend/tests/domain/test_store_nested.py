"""The database handle's connections: a query made inside another on the same thread runs instead of waiting for ever.
Python can close an abandoned generator (a streamed response whose reader went away) in the middle of any query, and
when its cleanup queries, the embedded engine's one connection is held by that very thread."""

from __future__ import annotations

import threading


def test_a_query_inside_another_on_the_same_thread_runs(db):
    got = []

    def nested():
        with db.conn():  # a query in flight on this thread holds the connection
            got.append(db.values("RETURN 1"))  # what a generator's cleanup, run by the garbage collector here, does

    t = threading.Thread(target=nested, daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive(), "the inner query waited for the connection its own thread holds"
    assert got == [[1]]


def test_the_connection_goes_back_to_the_pool_after_nested_queries(db):
    with db.conn() as c:
        assert db.values("RETURN 2") == [2]
        with db.conn() as inner:
            assert db.values("RETURN 3") == [3]
            if db.embedded:
                assert inner is c
    # free again: another thread gets it at once
    got = []
    t = threading.Thread(target=lambda: got.append(db.values("RETURN 4")), daemon=True)
    t.start()
    t.join(10)
    assert got == [[4]]
