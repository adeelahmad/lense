"""A tiny OpenAI-compatible server for tests: streams chat answers, returns JSON for json_schema requests."""

import http.server
import json
import re
import threading


class Handler(http.server.BaseHTTPRequestHandler):
    seen = []
    models = ["fake", "fake-large"]  # what GET /models lists
    tool_script = []  # assistant messages to return, in order, when a request offers tools
    reject_tools = False  # behave like a server whose model can't call tools

    def _json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # GET /models
        return self._json({"object": "list", "data": [{"id": m, "object": "model"} for m in Handler.models]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.seen.append(body)
        if body.get("tools"):
            if Handler.reject_tools:
                data = b'{"error": "tools are not supported by this model"}'
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            msg = Handler.tool_script.pop(0) if Handler.tool_script else {"content": "Nothing more to add."}
            return self._json(
                {"choices": [{"message": {"role": "assistant", "content": msg.get("content"), "tool_calls": msg.get("tool_calls")}}]}
            )
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in ["The shipment ", "leaves on Friday [1]."]:
                self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        schema = ((body.get("response_format") or {}).get("json_schema") or {}).get("schema") or {}
        if "verdicts" in schema.get("properties", {}):
            claims = [l[2:] for l in body["messages"][-1]["content"].splitlines() if l.startswith("- ")]
            content = json.dumps({"verdicts": [{"claim": c, "supported": i == 0} for i, c in enumerate(claims)]})
        elif "sentiment" in schema.get("properties", {}):
            # lines start with their time ([0:12]), or a document's with their page ([p. 2])
            times = re.findall(r"^\[(\d+(?::\d+)+|p\. \d+)\]", body["messages"][-1]["content"], re.M) or ["0:00"]
            content = json.dumps(
                {
                    "summary": "Alice and Bob talk about the capsid.",
                    "key_points": [{"text": "The capsid model beat the benchmark", "at": times[0]}, {"text": "No time given", "at": ""}],
                    "topics": ["capsid"],
                    "action_items": [{"text": "Send the capsid samples", "who": "Alice", "at": f"[{times[-1]}]"}, "Plain follow-up"],
                    "people": ["Alice"],
                    "sentiment": "happy",
                    "importance": 9,
                }
            )
        elif body.get("response_format"):
            content = json.dumps(
                {
                    "tldr": "Capsid samples ship Friday.",
                    "decisions": ["Ship Friday"],
                    "action_items": [{"owner": "Alice", "task": "Send the capsid samples"}],
                    "open_questions": [],
                }
            )
        else:
            content = "OK"
        data = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def start():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"
