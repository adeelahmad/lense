"""Auto-Ontology reuses the existing private, sealed MCP extension and its agent/API paths."""

from __future__ import annotations

import io
import json

import pytest

from app.domain import auto_ontology, extensions, mcp_servers
from tests.api._assist import Assist


def test_auto_ontology_is_private_sealed_and_callable(app, db, cfg, folder, new_client, monkeypatch):
    calls = []

    def opened(server, path, body=None):
        assert server["headers"] == {"x-api-key": "ontology-secret"}
        calls.append((path, body))
        if path == "/api/chat/completions":
            return io.BytesIO(
                b'data: {"type":"result","answer":{"response":"3 orders","sql_code":"SELECT COUNT(*) FROM orders","sql_response_from_db":[{"count":3}]}}\n\n'
            )
        return io.BytesIO(json.dumps({"terms": [{"id": "orders", "name": "Orders"}], "total": 1}).encode())

    monkeypatch.setattr(auto_ontology, "_open", opened)
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]
    ce, he = s.cl["editor"]
    presets = ca.get("/api/v1/extensions/mcp/presets", headers=ha).json()["presets"]
    preset = next(p for p in presets if p["name"] == "auto_ontology")
    spec, missing = mcp_servers.filled(
        preset["spec"], {"AUTO_ONTOLOGY_URL": "https://ontology.example.com", "AUTO_ONTOLOGY_API_TOKEN": "ontology-secret"}
    )
    assert not missing and preset["needs"] is None
    manifest = {"name": "auto_ontology", "kind": "mcp", "spec": spec}
    assert ce.post("/api/v1/extensions", headers=he, json={"manifest": manifest}).status_code == 400
    assert not calls
    tried = ca.post("/api/v1/extensions/mcp/test", headers=ha, json={"spec": spec})
    assert tried.status_code == 200, tried.text
    assert len(tried.json()["tools"]) == 5
    created = ca.post("/api/v1/extensions", headers=ha, json={"manifest": manifest})
    assert created.status_code == 200, created.text
    eid = created.json()["id"]
    saved = extensions.get(db, eid)
    assert saved["visibility"] == "private"
    assert mcp_servers.is_sealed(saved["spec"]["headers"]["x-api-key"])
    shown = ca.get(f"/api/v1/extensions/{eid}", headers=ha).json()
    assert shown["spec"]["adapter"] == "auto_ontology"
    assert shown["spec"]["headers"] == {"x-api-key": "••••"}
    assert "ontology-secret" not in json.dumps(shown)
    tried_saved = ca.post("/api/v1/extensions/mcp/test", headers=ha, json={"spec": shown["spec"], "extension": eid})
    assert tried_saved.status_code == 200, tried_saved.text
    assert eid not in [g["id"] for g in ce.get("/api/v1/extensions", headers=he).json()]
    denied = ce.post(
        f"/api/v1/extensions/{eid}/test", headers=he, json={"tool": "auto_ontology__ask_question", "args": {"question": "Orders?"}}
    )
    assert denied.status_code in (403, 404)
    result = ca.post(
        f"/api/v1/extensions/{eid}/test", headers=ha, json={"tool": "auto_ontology__ask_question", "args": {"question": "Orders?"}}
    )
    assert result.status_code == 200, result.text
    assert result.json()["output"]["json"]["rows"] == [{"count": 3}]
    assert calls[-1] == ("/api/chat/completions", {"question": "Orders?", "prediction": False})
    ledger = db.one("SELECT * FROM activity WHERE action = 'auto_ontology.ask_question' ORDER BY at DESC LIMIT 1")
    assert ledger["unpriced"] is True
    assert "ontology-secret" not in json.dumps(ledger, default=str)
    # Masked save preserves the adapter/token; changing transport cannot reuse its sealed credential.
    updated = ca.post(f"/api/v1/extensions/{eid}/versions", headers=ha, json={"text": shown["manifest"]})
    assert updated.status_code == 200, updated.text
    changed = {"name": "auto_ontology", "kind": "mcp", "spec": {"url": spec["url"], "headers": {"x-api-key": "••••"}}}
    with pytest.raises(ValueError, match="somewhere else"):
        mcp_servers.opened(db, changed, eid, extensions.get(db, eid))


def test_auto_ontology_config_and_run_keep_the_adapter(monkeypatch):
    parsed = mcp_servers.read(
        json.dumps(
            {
                "mcpServers": {
                    "auto_ontology": {
                        "adapter": "auto_ontology",
                        "url": "https://ontology.example.com",
                        "headers": {"x-api-key": "secret"},
                    }
                }
            }
        )
    )
    server = parsed["servers"][0]
    assert server["spec"]["adapter"] == "auto_ontology"
    monkeypatch.setattr(auto_ontology, "discover", lambda _: auto_ontology.TOOLS)
    spec = extensions.check_spec("mcp", server["spec"], {"admin": True})
    tools = extensions.mcp_tools({"name": server["name"], "spec": spec}, ext=7)
    assert len(tools) == 5
    assert all(t["spec"]["run"]["adapter"] == "auto_ontology" for t in tools)
    assert all(t["spec"]["effect"] == "read" for t in tools)
    spec["off"] = ["ask_question"]
    assert len(extensions.mcp_tools({"name": server["name"], "spec": spec}, ext=7)) == 4
