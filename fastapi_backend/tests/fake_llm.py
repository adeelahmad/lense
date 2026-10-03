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
    blind = False  # behave like a server whose model can't see images
    usage = None  # token counts to report with each answer (and as a streamed answer's last chunk), like OpenAI

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
        if self.path.endswith("/embeddings"):
            return self._embeddings(body)
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
            if Handler.usage:
                self.wfile.write(f"data: {json.dumps({'choices': [], 'usage': Handler.usage})}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        seen = _picture(body)
        if seen and Handler.blind:
            data = b'{"error": "this model does not support image input"}'
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if seen:
            return self._json({"choices": [{"message": {"content": _describe(seen)}}]})
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
        elif "judgements" in schema.get("properties", {}):
            # graph tidying: sure that the first pair is one thing, less sure of the rest, and the last isn't
            pairs = re.findall(r"^(\d+)\. a:", body["messages"][-1]["content"], re.M)
            content = json.dumps(
                {
                    "judgements": [
                        {
                            "pair": int(k),
                            "same": i < len(pairs) - 1 or i == 0,
                            "confidence": 0.97 if i == 0 else 0.6,
                            "keep": "a",
                            "why": "spelling",
                        }
                        for i, k in enumerate(pairs)
                    ]
                }
            )
        elif "mappings" in schema.get("properties", {}):
            # entity matching: a name goes to the entity whose description says it, else the first other answer offered
            text = body["messages"][-1]["content"]
            ents = re.findall(r"^e(\d+): .*? - (.*)$", text, re.M)
            names = re.findall(r'^(\d+)\. "(.*?)"', text, re.M)
            other = re.findall(r'"(new|unlabeled|unknown)"', text.split("Answers:", 1)[1].split("\n", 1)[0])
            out = []
            for i, name in names:
                hit = next((e for e, d in ents if name.lower() in d.lower()), None)
                out.append({"name": int(i), "to": f"e{hit}" if hit else other[0], "confidence": 0.9})
            content = json.dumps({"mappings": out})
        elif "entities" in schema.get("properties", {}):
            content = json.dumps(
                {
                    "entities": [
                        {"name": "Dave", "type": "PERSON", "line": 1},
                        {"name": "capsid samples", "type": "PRODUCT"},
                        {"name": "", "type": "ORG"},
                    ]
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
        extra = {"usage": Handler.usage, "model": body.get("model")} if Handler.usage else {}
        data = json.dumps({"choices": [{"message": {"content": content}}], **extra}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _embeddings(self, body):
        Handler.embedded.append(body)
        if not Handler.embeddings or Handler.embed_fail:
            data = b'{"error": "model not found"}'
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
        return self._json(
            {"object": "list", "model": body["model"], "data": [{"index": i, "embedding": embed(t)} for i, t in enumerate(texts)]}
        )

    def log_message(self, *a):
        pass


Handler.embeddings = False  # answer POST /embeddings (else 404, like a server without an embedding model)
Handler.embed_fail = False
Handler.embedded = []  # the embeddings requests seen

# A toy embedding model: one dimension per topic, which its words (and their synonyms) point along, so texts about the
# same thing in different words come out alike; other words spread thinly over the rest, so they still differ a little.
TOPICS = [
    {"money", "cash", "afford", "rent", "budget", "finances", "financial", "broke", "salary", "debt", "expensive", "bills"},
    {"travel", "trip", "flight", "airport", "holiday", "vacation", "journey", "abroad", "plane", "luggage"},
    {"sick", "ill", "illness", "doctor", "fever", "hospital", "health", "flu", "medicine", "unwell"},
    {"food", "dinner", "cook", "recipe", "kitchen", "lunch", "meal", "eat", "pasta", "hungry"},
    {"capsid", "protein", "virus", "vector", "gene", "therapy", "aav", "benchmark", "model"},
    {"ship", "shipment", "delivery", "deliver", "parcel", "courier", "send", "samples", "friday"},
]
EXTRA = 8


def embed(text):
    import math
    import zlib

    v = [0.0] * (len(TOPICS) + EXTRA)
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in ("search", "query", "document"):  # the prefixes some models are given
            continue
        hit = [k for k, t in enumerate(TOPICS) if w in t]
        for k in hit:
            v[k] += 1.0
        if not hit:
            v[len(TOPICS) + zlib.crc32(w.encode()) % EXTRA] += 0.15
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v] if any(v) else [1.0 / math.sqrt(len(v))] * len(v)


def _picture(body):
    """The picture a request shows the model (the last message's image_url part, a data URI), if any."""
    content = (body.get("messages") or [{}])[-1].get("content")
    parts = [p for p in content if isinstance(p, dict) and p.get("type") == "image_url"] if isinstance(content, list) else []
    return parts[0]["image_url"]["url"] if parts else None


def _describe(uri):
    """What the picture looks like, by its colour: a page of text on white, a yellow scene, or a dark blue one."""
    import base64
    import io

    from PIL import Image, ImageStat

    img = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).convert("RGB")
    r, g, b = ImageStat.Stat(img).mean
    if min(r, g, b) > 200:
        return "A white page with a few lines of black   printed\ntext."
    if r > 150 and g > 150:
        return "A yellow wall with large black letters on it."
    return "A dark blue screen with white lettering in the middle."


def start():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"
