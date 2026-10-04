"""Opt-in telemetry: OpenTelemetry traces and metrics, sent only to an OTLP endpoint an admin sets.

Off by default, and nothing is collected or sent while it is off: no phone-home, no built-in endpoint. Turned on
(Settings → Telemetry, the setup wizard, or LENS_TELEMETRY=on with LENS_TELEMETRY_ENDPOINT in .env), each process
(the API and every `lens worker`) sends what it does to that endpoint over OTLP/HTTP, typically a collector on the same
machine:

* traces: API requests (by route template), jobs and their steps, routines, workflow runs and model calls;
* metrics: request, step and model-call durations, job and routine outcomes, model tokens and estimated cost.

Spans and metrics carry no personal content: no transcript or prompt text, no file names or titles, no namespace names,
no client addresses or user ids; only route templates, step types, record ids, model names, token counts and timings.

The OpenTelemetry SDK is imported only when telemetry is on. Settings are re-read when someone saves them
(settings.Settings.current), so turning it on or off applies without a restart.
"""

from __future__ import annotations

import atexit
import logging
import re
import threading
import time
import urllib.parse
import uuid
from contextlib import contextmanager

log = logging.getLogger("lens")

_lock = threading.Lock()
_state = {"key": None, "tracer": None, "meter": None, "tp": None, "mp": None, "instruments": {}, "cfg": {}}
_role = ["api"]
_instance = str(uuid.uuid4())  # service.instance.id: random per process, so no host name leaves the machine
_last = {"traces": None, "metrics": None}  # the last export from this process: {at, ok, error}
HEADER_RX = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")


def parse_headers(text):
    """`key=value,key2=value2` (OTEL_EXPORTER_OTLP_HEADERS' form, values URL-encoded) to a dict; ValueError if malformed."""
    out = {}
    for part in (text or "").split(","):
        if not part.strip():
            continue
        k, sep, v = part.partition("=")
        k = k.strip()
        if not sep or not HEADER_RX.match(k):
            raise ValueError("telemetry.headers is key=value pairs separated by commas, like Authorization=Bearer%20abc")
        out[k] = urllib.parse.unquote(v.strip())
    return out


def _section(cfg):
    return (cfg or {}).get("telemetry") or {}


def _wanted(t):
    """The settings that decide what is set up; None when nothing should run."""
    if not t.get("enabled") or not t.get("endpoint"):
        return None
    if not (t.get("traces") or t.get("metrics")):
        return None
    return (
        t["endpoint"].rstrip("/"),
        t.get("headers") if isinstance(t.get("headers"), str) else None,
        bool(t.get("traces")),
        bool(t.get("metrics")),
        float(t.get("sample_ratio") if t.get("sample_ratio") is not None else 1.0),
        int(t.get("export_seconds") or 60),
        t.get("service_name") or "lens",
        _role[0],
    )


def prices(cfg):
    """USD per million tokens by model: telemetry.prices, over the assistant's prices for the configured model
    (Settings → AI assistant), so a price set there needn't be set twice."""
    ai, model = (cfg or {}).get("ai") or {}, ((cfg or {}).get("llm") or {}).get("model")
    out = {}
    if model and ai.get("price_in") is not None and ai.get("price_out") is not None:
        out[model] = {"input": ai["price_in"], "output": ai["price_out"]}
    return {**out, **(_section(cfg).get("prices") or {})}


def set_role(role):
    """What this process is (api, worker, watcher): sent as lens.process.role."""
    _role[0] = role


def apply(cfg):
    """Start, change or stop telemetry to match the configuration. Cheap when nothing changed."""
    t = _section(cfg)
    want = _wanted(t)
    _state["cfg"] = {"prices": prices(cfg)}
    if want == _state["key"]:
        return
    with _lock:
        if want == _state["key"]:
            return
        # the old providers send what they hold in the background: a request that picked up the change (this runs
        # under settings.Settings.current) never waits on a collector that is slow or gone
        _stop(wait=False)
        _last.update(traces=None, metrics=None)
        if want is not None:
            try:
                _start(*want)
            except Exception as e:  # noqa: BLE001 - telemetry never stops the archive
                log.warning("telemetry: couldn't start (%s: %s); carrying on without it", type(e).__name__, e)
                _stop(wait=False)
        elif _state["key"] is not None:
            log.info("telemetry: off")
        _state["key"] = want


def _stop(wait=True):
    providers = [p for p in (_state["tp"], _state["mp"]) if p is not None]
    _state.update(tracer=None, meter=None, tp=None, mp=None, instruments={})

    def close():
        for p in providers:
            try:
                p.shutdown()
            except Exception:  # noqa: BLE001 - shutting down a provider whose endpoint is gone
                pass

    if wait:
        close()
    elif providers:
        threading.Thread(target=close, daemon=True, name="telemetry-stop").start()


def _note(kind, ok, error=None):
    _last[kind] = {"at": time.time(), "ok": ok, "error": error}


def _start(endpoint, headers, traces, metrics, ratio, every, service, role):
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import MetricExportResult, PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    from . import __version__

    hdrs = parse_headers(headers) if headers else {}
    resource = Resource.create(
        {"service.name": service, "service.version": __version__, "service.instance.id": _instance, "lens.process.role": role}
    )

    class Spans(OTLPSpanExporter):
        def export(self, spans):
            try:
                r = super().export(spans)
            except Exception as e:  # noqa: BLE001
                _note("traces", False, f"{type(e).__name__}: {e}"[:300])
                raise
            _note("traces", r == SpanExportResult.SUCCESS, None if r == SpanExportResult.SUCCESS else "the endpoint refused the traces")
            return r

    class Metrics(OTLPMetricExporter):
        def export(self, metrics_data, timeout_millis=10000, **kw):
            try:
                r = super().export(metrics_data, timeout_millis=timeout_millis, **kw)
            except Exception as e:  # noqa: BLE001
                _note("metrics", False, f"{type(e).__name__}: {e}"[:300])
                raise
            ok = r == MetricExportResult.SUCCESS
            _note("metrics", ok, None if ok else "the endpoint refused the metrics")
            return r

    if traces:
        tp = TracerProvider(resource=resource, sampler=ParentBased(TraceIdRatioBased(max(0.0, min(1.0, ratio)))))
        tp.add_span_processor(BatchSpanProcessor(Spans(endpoint=endpoint + "/v1/traces", headers=hdrs, timeout=10)))
        _state.update(tp=tp, tracer=tp.get_tracer("lens", __version__))
    if metrics:
        reader = PeriodicExportingMetricReader(
            Metrics(endpoint=endpoint + "/v1/metrics", headers=hdrs, timeout=10), export_interval_millis=max(5, every) * 1000
        )
        mp = MeterProvider(resource=resource, metric_readers=[reader])
        _state.update(mp=mp, meter=mp.get_meter("lens", __version__))
    log.info(
        "telemetry: on, sending %s to %s",
        " and ".join(x for x, on in (("traces", traces), ("metrics", metrics)) if on),
        _safe_url(endpoint),
    )


def _safe_url(url):
    """The endpoint without any user:password in it, for logs and the status."""
    try:
        p = urllib.parse.urlsplit(url)
        return urllib.parse.urlunsplit(
            (p.scheme, p.hostname + (f":{p.port}" if p.port else "") if p.hostname else p.netloc, p.path, "", "")
        )
    except ValueError:
        return "(invalid address)"


def flush(timeout_millis=10000):
    """Send what is buffered now (tests; a worker about to exit)."""
    for p in (_state["tp"], _state["mp"]):
        if p is not None:
            p.force_flush(timeout_millis)


def shutdown():
    """Send what is buffered and stop (process exit)."""
    with _lock:
        _stop()
        _state["key"] = None


atexit.register(shutdown)


def active():
    return _state["tracer"] is not None or _state["meter"] is not None


def status(cfg):
    """What this process does with telemetry, for Settings."""
    t = _section(cfg)
    return {
        "enabled": bool(t.get("enabled")),
        "endpoint": _safe_url(t["endpoint"]) if t.get("endpoint") else None,
        "traces": bool(_state["tracer"]),
        "metrics": bool(_state["meter"]),
        "last_traces": _last["traces"],
        "last_metrics": _last["metrics"],
    }


# ---------- spans ----------
class _NoSpan:
    """What a span is while telemetry is off: nothing."""

    def set_attribute(self, key, value):
        pass

    def set_attributes(self, attrs):
        pass

    def record_exception(self, e):
        pass

    def set_status(self, *a, **k):
        pass

    def update_name(self, name):
        pass

    def end(self):
        pass

    def is_recording(self):
        return False


NO_SPAN = _NoSpan()


def _clean(attrs):
    return {k: v for k, v in (attrs or {}).items() if v is not None}


@contextmanager
def span(name, attrs=None, kind=None):
    """A span around a block (the current span while it runs), or nothing while traces are off. An exception that
    leaves the block marks the span failed with its type (not its message, which may quote content) and goes on."""
    tracer = _state["tracer"]
    if tracer is None:
        yield NO_SPAN
        return
    from opentelemetry.trace import SpanKind

    with tracer.start_as_current_span(
        name, kind=kind or SpanKind.INTERNAL, attributes=_clean(attrs), record_exception=False, set_status_on_exception=False
    ) as s:
        try:
            yield s
        except BaseException as e:
            fail(s, e)
            raise


def start_span(name, attrs=None, kind=None):
    """A span that isn't made current (for generators, which may resume in another context); end() it."""
    tracer = _state["tracer"]
    if tracer is None:
        return NO_SPAN
    from opentelemetry.trace import SpanKind

    return tracer.start_span(name, kind=kind or SpanKind.INTERNAL, attributes=_clean(attrs))


def fail(s, e):
    """Mark a span failed with an exception's type (not its message, which may quote content)."""
    if not s.is_recording():
        return
    from opentelemetry.trace import Status, StatusCode

    s.set_attribute("error.type", type(e).__name__)
    s.set_status(Status(StatusCode.ERROR, type(e).__name__))


# ---------- metrics ----------
def _instrument(kind, name, unit, description):
    meter = _state["meter"]
    if meter is None:
        return None
    inst = _state["instruments"].get(name)
    if inst is None:
        make = {"counter": meter.create_counter, "histogram": meter.create_histogram}[kind]
        inst = _state["instruments"][name] = make(name, unit=unit, description=description)
    return inst


METRICS = {
    "http.server.request.duration": ("histogram", "s", "API request duration"),
    "lens.job.step.duration": ("histogram", "s", "How long a job's step took"),
    "lens.jobs": ("counter", "{job}", "Job runs, by how they ended"),
    "lens.routine.runs": ("counter", "{run}", "Routine runs, by how they ended"),
    "lens.workflow.runs": ("counter", "{run}", "Workflow runs, by how they ended"),
    "gen_ai.client.operation.duration": ("histogram", "s", "Model call duration"),
    "gen_ai.client.token.usage": ("histogram", "{token}", "Tokens per model call"),
    "lens.llm.cost": ("counter", "USD", "Estimated model cost, from the prices set in Settings → Telemetry"),
}


def record(name, value, attrs=None):
    """Add to a counter or record in a histogram (see METRICS); nothing while metrics are off."""
    kind, unit, description = METRICS[name]
    inst = _instrument(kind, name, unit, description)
    if inst is None:
        return
    try:
        (inst.add if kind == "counter" else inst.record)(value, _clean(attrs))
    except Exception:  # noqa: BLE001 - a metric never breaks the work it measures
        pass


# ---------- model calls ----------
def cost(model, input_tokens, output_tokens, prices=None):
    """Estimated cost in USD from the per-million-token prices for the model, or None when it has no price."""
    p = (prices if prices is not None else _state["cfg"].get("prices") or {}).get(model)
    if not isinstance(p, dict):
        return None
    return round((input_tokens or 0) / 1e6 * float(p.get("input") or 0) + (output_tokens or 0) / 1e6 * float(p.get("output") or 0), 8)


class ModelCall:
    """One call to the model server: a client span (gen_ai.* attributes), its duration, tokens and estimated cost, and
    a row in the activity ledger (activity.py) whether or not telemetry is on. Neither the prompt nor the reply is
    recorded."""

    def __init__(self, cfg, payload, operation="chat", current=True):
        from . import activity  # the ledger counts every call, whether or not telemetry is on

        self.model = payload.get("model")
        self.operation, self.t0, self.done, self.response_model, self.usage = operation, time.monotonic(), False, None, None
        self.ledger = activity.call(f"model.{operation}", cfg, self.model, price=activity.token_cost(cfg))
        self.on = active()
        if not self.on:
            return
        l = (cfg or {}).get("llm") or {}
        server = urllib.parse.urlsplit(l.get("base_url") or "")
        attrs = {
            "gen_ai.operation.name": operation,
            "gen_ai.provider.name": "openai",  # the OpenAI-compatible API, whoever serves it
            "gen_ai.request.model": self.model,
            "gen_ai.request.temperature": payload.get("temperature"),
            "gen_ai.request.max_tokens": payload.get("max_tokens"),
            "gen_ai.output.type": "json" if payload.get("response_format") else "text",
            "lens.llm.tools": len(payload.get("tools") or []) or None,
            "lens.llm.stream": True if payload.get("stream") else None,
            "server.address": server.hostname,
            "server.port": server.port,
        }
        from opentelemetry.trace import SpanKind

        name = f"{operation} {self.model}"
        if current:
            self._cm = span(name, attrs, SpanKind.CLIENT)
            self.span = self._cm.__enter__()
        else:
            self._cm, self.span = None, start_span(name, attrs, SpanKind.CLIENT)

    def reply(self, j):
        """Note the usage and model of a reply (a parsed JSON body or a streamed chunk); returns it."""
        if isinstance(j, dict):
            if isinstance(j.get("usage"), dict):
                self.usage = j["usage"]
            if isinstance(j.get("model"), str):
                self.response_model = j["model"]
        return j

    def end(self, error=None):
        if self.done:
            return
        self.done = True
        u = self.usage or {}
        tin, tout = u.get("prompt_tokens", u.get("input_tokens")), u.get("completion_tokens", u.get("output_tokens"))
        self.ledger.usage(tin, tout)
        self.ledger.end(error)
        if not self.on:
            return
        seconds = time.monotonic() - self.t0
        base = {"gen_ai.operation.name": self.operation, "gen_ai.provider.name": "openai", "gen_ai.request.model": self.model}
        if self.response_model:
            base["gen_ai.response.model"] = self.response_model
            self.span.set_attribute("gen_ai.response.model", self.response_model)
        if error is not None:
            fail(self.span, error)
            record("gen_ai.client.operation.duration", seconds, {**base, "error.type": type(error).__name__})
        else:
            record("gen_ai.client.operation.duration", seconds, base)
        if isinstance(tin, int):
            self.span.set_attribute("gen_ai.usage.input_tokens", tin)
            record("gen_ai.client.token.usage", tin, {**base, "gen_ai.token.type": "input"})
        if isinstance(tout, int):
            self.span.set_attribute("gen_ai.usage.output_tokens", tout)
            record("gen_ai.client.token.usage", tout, {**base, "gen_ai.token.type": "output"})
        if isinstance(tin, int) or isinstance(tout, int):
            usd = cost(self.model, tin if isinstance(tin, int) else 0, tout if isinstance(tout, int) else 0)
            if usd is not None:
                self.span.set_attribute("lens.llm.cost_usd", usd)
                record("lens.llm.cost", usd, base)
        if self._cm is not None:
            self._cm.__exit__(None, None, None)
        else:
            self.span.end()

    def __enter__(self):
        return self

    def __exit__(self, et, e, tb):
        self.end(e)
        return False


def model_call(cfg, payload, operation="chat", current=True):
    """`with model_call(cfg, payload) as call: ... call.reply(json)`: a measured model call (only the ledger's row while
    telemetry is off)."""
    return ModelCall(cfg, payload, operation, current)


# ---------- the API ----------
def _route_template(scope) -> str:
    """The matched route's full template (`/api/v1/recordings/{rid}`). Since FastAPI 0.13x, `scope["route"]` is the
    route as its router declared it, without the prefixes it was included under; FastAPI keeps the full template on
    the effective route context it records in the scope."""
    ctx = (scope.get("fastapi") or {}).get("effective_route_context")
    return getattr(ctx, "path_format", None) or getattr(scope.get("route"), "path", None) or "unmatched"


class Middleware:
    """A server span and a duration for each API request, named by its route template (`GET /api/v1/recordings/{rid}`),
    never by its path or query, which can carry names. Installed innermost, where the router has chosen the route."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not active():
            await self.app(scope, receive, send)
            return
        from opentelemetry import context, trace
        from opentelemetry.trace import SpanKind, Status, StatusCode

        method = scope.get("method") or "GET"
        status = [None]

        async def sending(message):
            if message["type"] == "http.response.start":
                status[0] = message["status"]
            await send(message)

        t0, s, error = time.monotonic(), start_span(method, {"http.request.method": method}, SpanKind.SERVER), None
        token = context.attach(trace.set_span_in_context(s)) if s.is_recording() else None
        try:
            await self.app(scope, receive, sending)
        except Exception as e:
            error = e
            raise
        finally:
            if token is not None:
                context.detach(token)
            route = _route_template(scope)
            code = status[0] or 500
            attrs = {"http.request.method": method, "http.route": route, "http.response.status_code": code}
            if error is not None:
                attrs["error.type"] = type(error).__name__
            s.update_name(f"{method} {route}")
            s.set_attributes(attrs)
            if error is not None:
                fail(s, error)
            elif code >= 500:
                s.set_status(Status(StatusCode.ERROR))
            s.end()
            record("http.server.request.duration", time.monotonic() - t0, attrs)


def test_export(cfg, timeout=5):
    """Send one test span to the configured endpoint now, from a provider of its own; (ok, error, ms)."""
    t = _section(cfg)
    if not t.get("endpoint"):
        return False, "set an endpoint first", None
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult

    from . import __version__

    try:
        hdrs = parse_headers(t.get("headers")) if isinstance(t.get("headers"), str) else {}
    except ValueError as e:
        return False, str(e), None
    got = {}

    class Once(OTLPSpanExporter):
        def export(self, spans):
            try:
                got["r"] = super().export(spans)
            except Exception as e:  # noqa: BLE001
                got["e"] = f"{type(e).__name__}: {e}"[:300]
                raise
            return got["r"]

    tp = TracerProvider(resource=Resource.create({"service.name": t.get("service_name") or "lens", "service.version": __version__}))
    tp.add_span_processor(SimpleSpanProcessor(Once(endpoint=t["endpoint"].rstrip("/") + "/v1/traces", headers=hdrs, timeout=timeout)))
    t0 = time.monotonic()
    try:
        with tp.get_tracer("lens").start_as_current_span("lens.telemetry.test"):
            pass
    finally:
        tp.shutdown()
    ms = int((time.monotonic() - t0) * 1000)
    if got.get("r") == SpanExportResult.SUCCESS:
        return True, None, ms
    return (
        False,
        got.get("e") or f"the endpoint at {_safe_url(t['endpoint'])} didn't accept the span (check the address and the collector's log)",
        ms,
    )
