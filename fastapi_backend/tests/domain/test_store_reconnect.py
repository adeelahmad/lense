"""A SurrealDB server connection that drops (a keepalive ping timeout, a server restart) is replaced, so the API keeps
working instead of failing every request until it restarts."""

from __future__ import annotations

import pytest

from app.domain import store
from tests.conftest import TEST_URL

server = pytest.mark.skipif(not TEST_URL.startswith(("ws://", "wss://")), reason="needs LENS_TEST_SURREAL_URL (a server)")


@server
def test_queries_work_after_every_connection_drops(db):
    db.q("UPSERT $r SET n = 1", r=store.R("seq", "probe"))
    for c in list(db._all):
        c.socket.close()  # what a keepalive ping timeout leaves behind
    for _ in range(len(db._all) + 1):
        assert db.values("SELECT VALUE n FROM $r", r=store.R("seq", "probe")) == [1]


class _Sock:
    class protocol:  # noqa: N801
        state = None


class _Conn:
    """A stand-in server connection: `fail` raises what a dropped websocket raises."""

    def __init__(self, fail=None, closed=False):
        from websockets.protocol import State

        self.fail, self.closed_calls = fail, 0
        self.socket = _Sock()
        self.socket.protocol = type("P", (), {"state": State.CLOSED if closed else State.OPEN})()

    def query(self, sql, v):
        if self.fail:
            raise self.fail
        return [7]

    def close(self):
        self.closed_calls += 1


def _db(monkeypatch, first):
    d = store.DB.__new__(store.DB)
    d.url, d.embedded = "ws://test", False
    d._pool = store.queue.LifoQueue()
    d._all = [first]
    d._pool.put(first)
    fresh = _Conn()
    monkeypatch.setattr(d, "_open", lambda: fresh)
    return d, fresh


def test_a_closed_connection_is_replaced_before_it_is_used(monkeypatch):
    dead = _Conn(closed=True)
    d, fresh = _db(monkeypatch, dead)
    assert d.values("SELECT 1") == [7]
    assert d._all == [fresh] and dead.closed_calls == 1


def test_a_connection_that_drops_mid_query_is_replaced_for_the_next(monkeypatch):
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close

    dropped = _Conn(fail=ConnectionClosedError(Close(1011, "keepalive ping timeout"), None))
    d, fresh = _db(monkeypatch, dropped)
    with pytest.raises(ConnectionClosedError):
        d.values("SELECT 1")
    assert d.values("SELECT 1") == [7]
    assert d._all == [fresh]


def test_an_unreachable_server_keeps_the_old_connection_until_it_is_back(monkeypatch):
    from websockets.exceptions import ConnectionClosedError

    dead = _Conn(fail=ConnectionClosedError(None, None), closed=True)
    d, fresh = _db(monkeypatch, dead)

    def down():
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(d, "_open", down)
    with pytest.raises(ConnectionClosedError):
        d.values("SELECT 1")
    assert d._all == [dead]
    monkeypatch.setattr(d, "_open", lambda: fresh)
    assert d.values("SELECT 1") == [7]
