"""The decision client (app/domain/decide.py): typed questions to a System One server through TypeSafe's SDK, answers
as probabilities, and failures that never reach a request."""

from __future__ import annotations

import time

import pytest

from app.domain import decide
from tests import fake_decide


@pytest.fixture
def jev(cfg):
    srv, url = fake_decide.start()
    cfg["decisions"].update(enabled=True, base_url=url, model="fake-jev", timeout=5)
    decide.recovered()
    yield fake_decide.Handler
    srv.shutdown()
    decide.recovered()


def test_questions_come_back_as_typed_answers(cfg, jev):
    out = decide.ask(
        cfg,
        {"text": "The ship left the harbour before the tide turned."},
        {
            "sea": decide.noul("Is it about a ship or a harbour?", yes="it names boats, ports or the sea"),
            "topic": decide.choice("What is it about?", {"sea": "ships harbour tide", "money": "price budget invoice"}),
            "vivid": decide.score("ship harbour tide", ["flat", "plain", "vivid"]),
        },
    )
    assert out["sea"]["type"] == "noul" and out["sea"]["p"] > 0.5 and 0.5 <= out["sea"]["confidence"] <= 1
    assert out["topic"]["choice"] == "sea" and set(out["topic"]["probabilities"]) == {"sea", "money"}
    assert abs(sum(out["topic"]["probabilities"].values()) - 1) < 0.01
    assert out["vivid"]["type"] == "score" and 0 <= out["vivid"]["score"] <= 2
    sent = jev.seen[-1]
    assert sent["model"] == "fake-jev" and sent["questions"]["sea"]["criteria"] == {"true": "it names boats, ports or the sea"}
    assert sent["questions"]["topic"]["type"] == "choice" and sent["questions"]["vivid"]["criteria"] == ["flat", "plain", "vivid"]
    assert decide.ask(cfg, "x", {}) == {}
    # several requests at once come back in order
    many = decide.ask_many(cfg, [(f"harbour {n}", {"q": decide.noul("harbour?")}) for n in range(5)])
    assert len(many) == 5 and all(m["q"]["p"] > 0.5 for m in many)


def test_the_key_is_the_apps_then_the_environments(cfg, jev, monkeypatch):
    q = {"q": decide.noul("harbour?")}
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    decide.ask(cfg, "harbour", q)
    assert jev.keys[-1] == "Bearer none" and decide.status(cfg)["key"] is False  # a gateway in front adds its own
    # TypeSafe's own variable is for TypeSafe's server only: it never goes to another host
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-env")
    decide.recovered()
    decide.ask(cfg, "harbour", q)
    assert jev.keys[-1] == "Bearer none"
    assert decide.endpoint({"decisions": {"enabled": True}})[:2] == (decide.HOSTED, "ts-env")
    monkeypatch.setenv("MY_JEV_KEY", "ts-named")
    cfg["decisions"]["api_key_env"] = "MY_JEV_KEY"
    decide.ask(cfg, "harbour", q)
    assert jev.keys[-1] == "Bearer ts-named"
    cfg["decisions"]["api_key"] = "ts-saved"
    decide.ask(cfg, "harbour", q)
    assert jev.keys[-1] == "Bearer ts-saved" and decide.status(cfg)["key"] is True
    assert decide.status(cfg) == {
        "enabled": True,
        "configured": True,
        "base_url": cfg["decisions"]["base_url"],
        "hosted": False,
        "model": "fake-jev",
        "key": True,
    }


def test_failures_are_decide_errors_and_a_failing_server_is_left_alone_for_a_while(cfg, jev):
    q = {"q": decide.noul("harbour?")}
    cfg["decisions"]["enabled"] = False
    assert not decide.configured(cfg) and not decide.uses(cfg, "rerank")
    with pytest.raises(decide.DecideError, match="no decision model is set up"):
        decide.ask(cfg, "x", q)
    cfg["decisions"]["enabled"] = True
    assert decide.uses(cfg, "rerank") and decide.threshold(cfg, "apply_above") == 0.85
    with pytest.raises(ValueError):
        decide.ask(cfg, "x", {str(n): decide.noul("?") for n in range(65)})
    with pytest.raises(ValueError):
        decide.choice("?", ["only"])
    with pytest.raises(ValueError):
        decide.score("?", ["one"])
    # an answer that isn't one of the options is no answer
    jev.script = {"topic": {"choice": "weather", "probabilities": {"weather": 1.0}, "confidence": 1.0}}
    with pytest.raises(decide.DecideError):
        decide.ask(cfg, "x", {"topic": decide.choice("?", ["sea", "money"])})
    jev.script = {}
    # the key is refused: said once, then the server isn't asked again for a while
    jev.fail = (401, {"detail": {"error_type": "authentication_error", "message": "Invalid API key"}})
    before = len(jev.seen)
    with pytest.raises(decide.DecideError, match="401"):
        decide.ask(cfg, "x", q)
    asked = len(jev.seen) - before
    with pytest.raises(decide.DecideError, match="401"):
        decide.ask(cfg, "x", q)
    assert len(jev.seen) - before == asked
    got = decide.ask_many(cfg, [("x", q), ("y", q)])
    assert all(isinstance(g, decide.DecideError) for g in got)
    jev.fail = None
    decide.recovered()
    assert decide.ask(cfg, "harbour", q)["q"]["p"] > 0.5
    # a server that isn't there
    cfg["decisions"]["base_url"] = "http://127.0.0.1:9"
    with pytest.raises(decide.DecideError, match="couldn't be reached") as e:
        decide.ask(cfg, "x", q, timeout=1)
    assert "127.0.0.1" not in str(e.value) and "127.0.0.1:9" in e.value.detail  # the address is for admins and the log
    # and it isn't tried again for a while: what waits on it carries on at once
    t0 = time.time()
    with pytest.raises(decide.DecideError, match="couldn't be reached"):
        decide.ask(cfg, "x", q, timeout=30)
    assert time.time() - t0 < 0.2
