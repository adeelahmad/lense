"""A tiny stand-in for the decision model (typesafe.ai's System One): answers each question with `Handler.answer`."""

import http.server
import json
import threading


class Handler(http.server.BaseHTTPRequestHandler):
    seen = []  # (headers, body) of each request
    answer = None  # {"choice", "confidence", "probabilities"}; None picks the first option, sure of it
    status = 200

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.seen.append((dict(self.headers), body))
        if Handler.status != 200:
            data = b'{"detail": {"error_type": "api_usage_error", "message": "Invalid request."}}'
        else:
            answers = {}
            for name, q in body["questions"].items():
                first = next(iter(q["criteria"]))
                answers[name] = Handler.answer or {"type": "choice", "choice": first, "confidence": 1.0, "probabilities": {first: 1.0}}
            data = json.dumps({"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 400, "output_tokens": 40}}).encode()
        self.send_response(Handler.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start(cfg, key="jev-key"):
    Handler.seen, Handler.answer, Handler.status = [], None, 200
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg["decisions"] = {**cfg.get("decisions", {}), "base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "api_key": key}
    return srv
