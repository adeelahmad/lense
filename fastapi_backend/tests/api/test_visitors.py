"""Visitors told apart behind the web app: in Docker the API sees every request come from the web app's container,
whose address changes when it's recreated. LENS_TRUSTED_PROXY_HOSTS names it, so its X-Forwarded-For counts and
throttles are per visitor, not one bucket for everyone."""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from tests.helpers import make_user

WEB_APP = "172.18.0.5"


@pytest.fixture
def web_app(app, monkeypatch):
    real = socket.getaddrinfo

    def lookup(host, *a, **kw):
        if host == "frontend":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (WEB_APP, 0))]
        if host in ("backend", "worker"):
            raise socket.gaierror("not in this network")
        return real(host, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    deps._PROXY_HOSTS.clear()
    yield TestClient(app, base_url="http://127.0.0.1", client=(WEB_APP, 50000))
    deps._PROXY_HOSTS.clear()


def bad_logins(client, visitor, n=8):
    for _ in range(n):
        r = client.post(
            "/api/v1/auth/login", json={"email": "ada@x.io", "password": "wrong"}, headers={"x-forwarded-for": f"203.0.113.250, {visitor}"}
        )
    return r.status_code


def test_through_the_web_app_each_visitor_has_its_own_throttle(db, web_app, monkeypatch):
    make_user(db, "ada@x.io", "right password 1", admin=True)
    monkeypatch.setenv("LENS_TRUSTED_PROXY_HOSTS", "frontend,backend,worker")
    bad_logins(web_app, "198.51.100.1")
    assert bad_logins(web_app, "198.51.100.1", 1) == 429
    # someone else, through the same web app, isn't locked out; the rightmost forwarded address counts, not one a
    # browser made up (203.0.113.250)
    r = web_app.post(
        "/api/v1/auth/login",
        json={"email": "ada@x.io", "password": "right password 1"},
        headers={"x-forwarded-for": "198.51.100.1, 198.51.100.2"},
    )
    assert r.status_code == 200, r.text


def test_without_it_the_web_app_is_one_visitor(db, web_app, monkeypatch):
    make_user(db, "ada@x.io", "right password 1", admin=True)
    monkeypatch.delenv("LENS_TRUSTED_PROXY_HOSTS", raising=False)
    bad_logins(web_app, "198.51.100.1")
    r = web_app.post(
        "/api/v1/auth/login", json={"email": "ada@x.io", "password": "right password 1"}, headers={"x-forwarded-for": "198.51.100.2"}
    )
    assert r.status_code == 429
