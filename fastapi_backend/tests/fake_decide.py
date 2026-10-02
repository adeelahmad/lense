"""A tiny System One server for tests (`POST /v1/systemone`, as TypeSafe's Jev answers it): typed answers worked out
from the words, so tests can tell what a decision should be without a model.

A yes/no question is likelier yes the more of the words of what's asked about are in the text it's asked of; a choice
picks the option that shares the most words with the state; a score counts them. `Handler.script` overrides any
question by its id (or by a word in its instructions) with a fixed answer.
"""

import http.server
import json
import re
import threading

WORD = re.compile(r"[a-z0-9']+")
STOP = set("the a an of to and or is are was were in on at for it this that with as by be about any does do not".split())


def words(x):
    if not isinstance(x, str):
        x = json.dumps(x)
    return {w for w in WORD.findall(x.lower()) if w not in STOP and len(w) > 2}


def _stem(ws):
    return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in ws}


def overlap(a, b):
    a, b = _stem(words(a)), _stem(words(b))
    return len(a & b) / max(1, min(len(a), len(b)))


class Handler(http.server.BaseHTTPRequestHandler):
    seen = []  # the request bodies, in order
    keys = []  # the Authorization header of each request (None without one)
    script = {}  # {question id or a word of its instructions: answer fields}
    fail = None  # (status, body) to answer every request with instead
    model = "fake-jev"

    def log_message(self, *a):
        pass

    def _send(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.seen.append(body)
        Handler.keys.append(self.headers.get("Authorization"))
        if Handler.fail:
            return self._send(*Handler.fail)
        if not self.path.endswith("/systemone"):
            return self._send(404, {"detail": "not found"})
        state, answers = body.get("state"), {}
        for qid, q in (body.get("questions") or {}).items():
            fixed = Handler.script.get(qid)
            if fixed is None:
                text = json.dumps(q.get("instructions"), ensure_ascii=False).lower()
                named = re.search(r"passage ([a-z]\d+)", text)
                if named and isinstance(state, dict):  # a word of the passage it's about counts too
                    text += " " + str((state.get("passages") or {}).get(named.group(1), "")).lower()
                fixed = next((v for k, v in Handler.script.items() if k in text), None)
            answers[qid] = {"type": q["type"], **(fixed if fixed is not None else self._answer(state, q))}
        self._send(200, {"model": Handler.model, "answers": answers, "usage": {"input_tokens": 100, "output_tokens": len(answers)}})

    @staticmethod
    def _answer(state, q):
        what, crit = q.get("instructions"), q.get("criteria")
        # a question about one of the state's passages names it: "… passage p3 …"
        named = re.search(r"passage ([a-z]\d+)", what) if isinstance(what, str) and isinstance(state, dict) else None
        passage = (state.get("passages") or {}).get(named.group(1)) if named else None
        if q["type"] == "noul":
            if passage is not None:
                subject = state.get("search_query") or state.get("question") or ""
                p = round(min(0.98, 0.04 + overlap(subject, passage)), 3)
            else:
                p = round(min(0.98, 0.04 + overlap(what, state)), 3)
            return {"noul": p, "confidence": round(max(p, 1 - p), 3)}
        if q["type"] == "choice":
            if passage is not None and "question" in state:  # an ask: yes when the passage shares the question's words
                pick = "yes" if overlap(state["question"], passage) >= 0.5 else "silent"
                return {"choice": pick, "probabilities": {k: (0.9 if k == pick else 0.05) for k in crit}, "confidence": 0.9}
            if passage is not None and "statement" in state:  # a check: it says it, says it isn't so, or doesn't say
                alike = overlap(state["statement"], passage)
                denies = (" not " in f" {passage.lower()} ") != (" not " in f" {state['statement'].lower()} ")
                pick = "silent" if alike < 0.5 else "contradicts" if denies else "supports"
                probs = {k: (0.9 if k == pick else 0.05) for k in crit}
                return {"choice": pick, "probabilities": probs, "confidence": 0.9}
            scores = {k: overlap(f"{k} {v or ''}", state) + 0.01 for k, v in crit.items()}
            total = sum(scores.values())
            probs = {k: round(v / total, 3) for k, v in scores.items()}
            best = max(probs, key=probs.get)
            return {"choice": best, "probabilities": probs, "confidence": probs[best]}
        level = min(len(crit) - 1, len(_stem(words(what)) & _stem(words(state))))
        probs = {str(i): (0.8 if i == level else round(0.2 / (len(crit) - 1), 4)) for i in range(len(crit))}
        expected = round(sum(int(i) * p for i, p in probs.items()), 4)
        return {"score": expected, "probabilities": probs, "legend": {str(i): c for i, c in enumerate(crit)}, "confidence": 0.8}


def start():
    Handler.seen, Handler.keys, Handler.script, Handler.fail = [], [], {}, None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
