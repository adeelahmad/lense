"""A tiny OTLP/HTTP collector for tests: keeps the spans and metric points it is sent, decoded."""

import http.server
import threading

from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


def _value(v):
    kind = v.WhichOneof("value")
    return getattr(v, kind) if kind else None


def _attrs(kvs):
    return {kv.key: _value(kv.value) for kv in kvs}


class Handler(http.server.BaseHTTPRequestHandler):
    spans = []  # {name, kind, attrs, status, resource}
    points = []  # {name, unit, attrs, value}
    headers = []  # the request headers of each export
    bodies = []  # the raw requests, to check nothing personal is in them
    refuse = False  # answer 400, like a collector that won't take what it's sent

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Handler.headers.append({k.lower(): v for k, v in self.headers.items()})
        Handler.bodies.append(body)
        if Handler.refuse:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/v1/traces":
            req = ExportTraceServiceRequest()
            req.ParseFromString(body)
            for rs in req.resource_spans:
                res = _attrs(rs.resource.attributes)
                for ss in rs.scope_spans:
                    for s in ss.spans:
                        Handler.spans.append(
                            {"name": s.name, "kind": s.kind, "attrs": _attrs(s.attributes), "status": s.status.code, "resource": res}
                        )
        elif self.path == "/v1/metrics":
            req = ExportMetricsServiceRequest()
            req.ParseFromString(body)
            for rm in req.resource_metrics:
                for sm in rm.scope_metrics:
                    for m in sm.metrics:
                        data = getattr(m, m.WhichOneof("data"))
                        for p in data.data_points:
                            value = p.sum if hasattr(p, "bucket_counts") else _number(p)
                            Handler.points.append({"name": m.name, "unit": m.unit, "attrs": _attrs(p.attributes), "value": value})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


def _number(p):
    kind = p.WhichOneof("value")
    return getattr(p, kind) if kind else None


def reset():
    Handler.spans, Handler.points, Handler.headers, Handler.bodies, Handler.refuse = [], [], [], [], False


def start():
    reset()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
