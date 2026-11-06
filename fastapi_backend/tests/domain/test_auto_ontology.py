"""The REST adapter's public contract. These tests also run with stdlib unittest, without a database or model."""

from __future__ import annotations

import http.client
import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from app.domain import auto_ontology as ontology

SERVER = {"adapter": "auto_ontology", "url": "https://ontology.example.com", "headers": {"x-api-key": "test-secret"}}


def reply(value):
    return io.BytesIO(json.dumps(value).encode())


def stream(answer=None):
    answer = answer or {"response": "Three orders", "sql_code": "SELECT COUNT(*) FROM orders", "sql_response_from_db": '[{"count":3}]'}
    return io.BytesIO(b": ping\r\n\r\ndata: " + json.dumps({"type": "result", "answer": answer}).encode() + b"\r\n\r\ndata: [DONE]\r\n\r\n")


class AutoOntologyTests(unittest.TestCase):
    def run_tool(self, name, args=None):
        return ontology.call({**SERVER, "tool": name}, args or {})["json"]

    def test_discovery_is_a_bounded_authenticated_read(self):
        with patch.object(ontology, "_open", return_value=reply({"terms": [], "total": 0})) as opened:
            tools = ontology.discover(SERVER)
        self.assertEqual(opened.call_args.args, (SERVER, "/api/terms?limit=1", None))
        self.assertEqual({t["name"] for t in tools}, {"check_readiness", "search_terms", "get_term", "check_answerable", "ask_question"})
        self.assertTrue(all(t["read_only"] for t in tools))
        tools[0]["schema"]["properties"]["bad"] = {}
        self.assertNotIn("bad", ontology.TOOLS[0]["schema"]["properties"])

    def test_only_a_fixed_gateway_and_api_token_are_allowed(self):
        bad_urls = [
            "https://user:secret@host",
            "https://host/api",
            "https://host?key=x",
            "https://host#x",
            "file:///tmp/x",
            "https://host\\@other",
            "https://host:bad",
            "https://host:0",
            "https://host\n",
            "https://ho st",
        ]
        for url in bad_urls:
            with self.subTest(url=url), self.assertRaises(ontology.OntologyError):
                ontology.config({**SERVER, "url": url})
        for change in (
            {"keep": True},
            {"command": "python"},
            {"headers": {"x-auto-ontology-user-id": "admin"}},
            {"headers": {"x-api-key": "x\r\ny"}},
            {"headers": {"x-api-key": "x", "Host": "elsewhere"}},
        ):
            with self.subTest(change=change), self.assertRaises(ontology.OntologyError):
                ontology.config({**SERVER, **change})
        self.assertEqual(ontology.config({**SERVER, "url": "http://localhost:3000/"})[0], "http://localhost:3000")

    def test_search_uses_encoded_parameters_and_bounded_pagination(self):
        with patch.object(ontology, "_json", return_value={"terms": [{"id": "t1", "name": "Revenue"}], "total": 1}) as request:
            result = self.run_tool("search_terms", {"query": "revenue & profit?", "offset": 7, "limit": 3})
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.call_args.args[1]).query)
        self.assertEqual(query, {"query": ["revenue & profit?"], "skip": ["7"], "limit": ["3"]})
        self.assertEqual(result["source"], SERVER["url"])
        for args in (
            {"limit": 101},
            {"limit": True},
            {"limit": 1.5},
            {"offset": -1},
            {"offset": 10001},
            {"query": "a" * 501},
            {"route": "/api/connections"},
        ):
            with self.subTest(args=args), patch.object(ontology, "_open") as opened, self.assertRaises(ontology.OntologyError):
                self.run_tool("search_terms", args)
            opened.assert_not_called()

    def test_term_ids_cannot_select_another_route(self):
        with patch.object(ontology, "_json", return_value={"data": {"id": "term 1"}}) as request:
            self.run_tool("get_term", {"term_id": "term 1"})
        self.assertEqual(request.call_args.args[1], "/api/terms/term%201")
        for term in (".", "..", "../connections", "%2e%2e", "x?y", "x#y", "x\\y", ""):
            with self.subTest(term=term), patch.object(ontology, "_open") as opened, self.assertRaises(ontology.OntologyError):
                self.run_tool("get_term", {"term_id": term})
            opened.assert_not_called()

    def test_readiness_does_not_expose_connection_metadata(self):
        with patch.object(
            ontology,
            "_json",
            side_effect=[{"calculated": True, "running": False}, {"count": 1, "data": [{"host": "private"}]}, {"count": 2}],
        ):
            result = self.run_tool("check_readiness")
        self.assertIs(result["ready"], True)
        self.assertNotIn("private", json.dumps(result))
        with patch.object(
            ontology,
            "_json",
            side_effect=[{"calculated": True, "running": False}, ontology.OntologyError("forbidden", 403), {"count": 1}],
        ):
            self.assertIsNone(self.run_tool("check_readiness")["ready"])
        with patch.object(ontology, "_json", side_effect=[{"calculated": True, "running": False}, {"count": 0}, {"count": 1}]):
            self.assertIs(self.run_tool("check_readiness")["ready"], False)
        with (
            patch.object(ontology, "_json", return_value={"calculated": "false", "running": False}),
            self.assertRaises(ontology.OntologyError),
        ):
            self.run_tool("check_readiness")

    def test_answerability_uses_only_the_question(self):
        with patch.object(ontology, "_json", return_value={"data": {"coverage": 0.5, "uncovered_entities": ["ad spend"]}}) as request:
            result = self.run_tool("check_answerable", {"question": "What was ad spend?"})
        self.assertEqual(request.call_args.args[1:], ("/api/question-entity-coverage", {"question": "What was ad spend?"}))
        self.assertEqual(result["data"]["uncovered_entities"], ["ad spend"])

    def test_question_returns_sql_rows_and_source_without_predictions_or_history(self):
        answer = {
            "response": "Three orders",
            "sql_code": "SELECT * FROM orders",
            "sql_response_from_db": [json.dumps([{"id": i} for i in range(3)])],
        }
        with patch.object(ontology, "_open", return_value=stream(answer)) as opened:
            result = self.run_tool("ask_question", {"question": "Show orders", "max_rows": 2})
        self.assertEqual(opened.call_args.args[1:], ("/api/chat/completions", {"question": "Show orders", "prediction": False}))
        self.assertEqual(result["rows"], [{"id": 0}, {"id": 1}])
        self.assertEqual(result["row_count"], 3)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["sql"], answer["sql_code"])
        self.assertEqual(result["source"], SERVER["url"])
        with self.assertRaises(ontology.OntologyError):
            self.run_tool("ask_question", {"question": "x", "prediction": True})

    def test_multiline_sse_and_final_unterminated_event(self):
        raw = b': ping\n\nevent: result\ndata: {"type":"result",\ndata: "answer":{"response":"done"}}'
        self.assertEqual(ontology._result(io.BytesIO(raw), time.monotonic()), {"response": "done"})

    def test_bad_or_missing_results_fail_without_retry_or_reflecting_errors(self):
        for raw in (
            b"data: [DONE]\n\n",
            b'data: {"type":"step"}\n\n',
            b"data: not-json\n\n",
            b'data: {"type":"result","answer":{}}\n\n',
            b'data: {"type":"error","message":"test-secret"}\n\n',
        ):
            with self.subTest(raw=raw), patch.object(ontology, "_open", return_value=io.BytesIO(raw)) as opened:
                with self.assertRaises(ontology.OntologyError) as error:
                    self.run_tool("ask_question", {"question": "x"})
                self.assertNotIn("test-secret", str(error.exception))
                opened.assert_called_once()
        with patch.object(ontology, "_open", side_effect=TimeoutError("test-secret")) as opened:
            with self.assertRaisesRegex(ontology.OntologyError, "interrupted") as error:
                self.run_tool("ask_question", {"question": "x"})
            self.assertNotIn("test-secret", str(error.exception))
            opened.assert_called_once()
        with patch.object(ontology, "_open", side_effect=http.client.IncompleteRead(b"test-secret")):
            with self.assertRaisesRegex(ontology.OntologyError, "interrupted") as error:
                self.run_tool("ask_question", {"question": "x"})
            self.assertNotIn("test-secret", str(error.exception))

    def test_size_and_time_limits_are_enforced(self):
        with patch.object(ontology, "MAX_BYTES", 16), self.assertRaisesRegex(ontology.OntologyError, "size limit"):
            ontology._result(io.BytesIO(b":" * 17), time.monotonic())
        with self.assertRaisesRegex(ontology.OntologyError, "time limit"):
            ontology._result(stream(), time.monotonic() - ontology.MAX_SECONDS - 1)
        with (
            patch.object(ontology, "MAX_BYTES", 10),
            patch.object(ontology, "_open", return_value=reply({"data": "long"})),
            self.assertRaisesRegex(ontology.OntologyError, "size limit"),
        ):
            ontology._json(SERVER, "/api/terms")

    def test_rows_preserve_shape_and_report_missing_data(self):
        for value in ({"count": 3}, '[{"count":3}]', ['[{"count":3}]']):
            self.assertEqual(ontology._rows(value, 25)["rows"], [{"count": 3}])
        self.assertTrue(ontology._rows("not JSON", 25)["rows_unavailable"])
        self.assertEqual(ontology._rows(None, 25)["row_count"], 0)

    def test_json_and_schema_failures_are_not_successes(self):
        for value in (b"no", b"[]", b"\xff"):
            with patch.object(ontology, "_open", return_value=io.BytesIO(value)), self.assertRaises(ontology.OntologyError):
                ontology.discover(SERVER)
        for tool, args in (("search_terms", {}), ("get_term", {"term_id": "id"}), ("check_answerable", {"question": "q"})):
            with patch.object(ontology, "_json", return_value={}), self.assertRaises(ontology.OntologyError):
                self.run_tool(tool, args)

    def test_redirects_never_receive_the_api_token(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("x-api-key")))
                self.send_response(302)
                self.send_header("Location", "/stolen")
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            target = {**SERVER, "url": f"http://127.0.0.1:{server.server_port}"}
            with self.assertRaises(ontology.OntologyError) as error:
                ontology._json(target, "/api/terms")
            self.assertEqual(error.exception.status, 302)
            self.assertEqual(seen, [("/api/terms", "test-secret")])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
