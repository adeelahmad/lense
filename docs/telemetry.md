# Telemetry

Lens can send traces and metrics about its own work to an [OpenTelemetry](https://opentelemetry.io/) collector, to see
how it performs: which API requests are slow, how long each job step takes, how often jobs fail, and how many tokens
model calls use and what they cost.

**It is off unless you turn it on.** Nothing is collected while it is off, and Lens has no built-in destination: it
never sends telemetry to the Lens project or anyone else. Turned on, it sends only to the endpoint you set, typically
a collector on the same machine or network, which can pass it on to Jaeger, Grafana Tempo and Prometheus, SigNoz,
Honeycomb, or whatever you use.

## Turning it on

Any of these:

- **Settings → Telemetry**: turn on **Send telemetry** and set the **OTLP endpoint**. **Send a test span** checks the
  address and headers first, even while it is off. The section shows whether it is on, where it sends, and how the
  last exports went.
- **The setup wizard** asks on a fresh install (its last step); it is off unless you choose on.
- **.env**, for the API and every worker:

  ```sh
  LENS_TELEMETRY=on                                 # off keeps it off, whatever Settings say
  LENS_TELEMETRY_ENDPOINT=http://localhost:4318     # the collector's OTLP/HTTP address
  LENS_TELEMETRY_HEADERS=Authorization=Bearer%20xyz # optional: key=value pairs, comma-separated, URL-encoded
  ```

  What .env sets wins over Settings, which show those fields locked.

Each process (the API, each `lens worker`, `lens watch`) follows the settings on its own and picks up a change within a
few seconds, with no restart. The endpoint is the collector's base address: traces go to `/v1/traces` and metrics to
`/v1/metrics` under it, over OTLP/HTTP with protobuf.

In Docker, `localhost` is the container itself: point the endpoint at a collector on the `lens` network (for example
`http://otel-collector:4318`, below) or at `http://host.docker.internal:4318` for one on the host.

## What is sent

| Signal | What | Attributes |
| --- | --- | --- |
| Trace | each API request, named by its route template (`GET /api/v1/recordings/{rid}`) | method, route, status code |
| Trace | each job, with a span per step (`step transcribe`) | job, recording and pipeline ids; step type and outcome |
| Trace | routines and their actions, workflow runs | routine, workflow and recording ids; action type; trigger |
| Trace | each call to the model server (`chat llama3.1`) | model, server host, temperature, tokens in and out, estimated cost |
| Metric | `http.server.request.duration` | method, route, status code |
| Metric | `lens.job.step.duration`, `lens.jobs` | step type, outcome |
| Metric | `lens.routine.runs`, `lens.workflow.runs` | outcome |
| Metric | `gen_ai.client.operation.duration`, `gen_ai.client.token.usage` | model, token type |
| Metric | `lens.llm.cost` (USD) | model |

Every span and metric carries `service.name` (Settings → Telemetry, default `lens`), `service.version`, a random
`service.instance.id` per process, and `lens.process.role` (`api`, `worker` or `watcher`). Model calls follow the
OpenTelemetry [GenAI conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/).

**Never sent:** transcript text, prompts or answers, file names, titles, namespace or speaker names, people, email
addresses, client IP addresses, request paths or query strings (only route templates), host names, or error messages
(only the error's type, since a message can quote content).

### Tokens and cost

Token counts come from the model server's reply (`usage`). Most servers report them; a streamed chat answer has them
only when the server sends usage in its last chunk.

Cost is an estimate from prices you set: **Model prices** in Settings → Telemetry, one line per model with its input
and output price in dollars per million tokens (`gpt-4o-mini 0.15 0.60`). The prices in Settings → AI assistant count
for the configured model too. A model without a price gets no cost, so local models cost nothing.

## Other settings

| Setting | Default | |
| --- | --- | --- |
| Traces / Metrics | on / on | send either or both |
| Share of traces kept | `1` | `0.1` keeps one trace in ten (head sampling) |
| Send metrics every | `60` seconds | |
| Service name | `lens` | how this server shows in your tracing tool |

In archive.yaml they live under `telemetry:` (`enabled`, `endpoint`, `traces`, `metrics`, `sample_ratio`,
`export_seconds`, `service_name`, `prices`).

## A collector next to Lens

A minimal [OpenTelemetry Collector](https://opentelemetry.io/docs/collector/) that takes Lens's traces and metrics and
prints them (swap the `debug` exporter for your backend's):

```yaml
# otel-collector.yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
exporters:
  debug:
    verbosity: basic
service:
  pipelines:
    traces: { receivers: [otlp], exporters: [debug] }
    metrics: { receivers: [otlp], exporters: [debug] }
```

Beside `docker-compose.prod.yml`, in a `docker-compose.override.yml` or with `-f`:

```yaml
services:
  otel-collector:
    image: otel/opentelemetry-collector:latest
    command: ["--config=/etc/otel-collector.yaml"]
    volumes:
      - ./otel-collector.yaml:/etc/otel-collector.yaml:ro
    networks: [lens]
```

Then set the endpoint to `http://otel-collector:4318` and turn telemetry on.
