# Prometheus

Lens can serve a [Prometheus](https://prometheus.io/) scrape endpoint at `/metrics`, so Prometheus, Grafana Agent,
VictoriaMetrics or any other scraper can chart what Lens is doing and what it costs: jobs and pipelines, imports, model
calls, tokens and cost, budgets, workers and the disk.

**It is off unless you turn it on**, and even then it answers only a scraper that sends the scrape token. It is a pull
endpoint: Lens sends nothing anywhere. For pushing traces and metrics to an OpenTelemetry collector instead, see
[Telemetry](telemetry.md); the two are independent.

## Prometheus and Grafana, ready made

Lens doesn't build its own charts: with Docker, the `monitoring` profile runs [Prometheus](https://prometheus.io/),
[node-exporter](https://github.com/prometheus/node_exporter) (the machine's CPU, memory, disk and network) and
[Grafana](https://grafana.com/oss/grafana/) next to Lens, already connected, with the Lens dashboard as Grafana's home
page. Add to `.env`:

```sh
COMPOSE_PROFILES=monitoring                 # with others, comma-separated (matterbridge,monitoring)
LENS_PROMETHEUS=on
LENS_PROMETHEUS_TOKEN=3f9c...               # openssl rand -hex 32
LENS_GRAFANA_URL=http://localhost:3001      # the address you open Grafana at; System health links to it
# GRAFANA_ADMIN_PASSWORD=...                # optional; otherwise admin/admin, and Grafana asks to change it
# LENS_METRICS_DAYS=30                      # how long Prometheus keeps history
```

then `docker compose -f docker-compose.prod.yml up -d`. Grafana listens where the web app does (`LENS_BIND`: this
machine only unless Lens was opened to the network) on port 3001 (`LENS_GRAFANA_PORT`). Anyone who reaches it can view
the dashboards, which hold no personal content; only Grafana's admin can change them. Prometheus and node-exporter are
reachable only inside Lens's Docker network.

**Admin → System health** shows the server now (CPU, memory, load, uptime) and links to Grafana for history.

On a small machine, the three add roughly 150-250 MB of memory; they are off unless the profile is on. Prometheus
(Apache 2.0), node-exporter (Apache 2.0) and Grafana OSS (AGPL 3.0) run as their own unmodified images, nothing of them
is built into Lens.

## Turning it on

- **Settings → Telemetry → Prometheus**: turn on **Prometheus endpoint** and set a **Scrape token** (16 to 200
  letters, digits, `.`, `_`, `~` and `-`; `openssl rand -hex 32` makes a good one). The token is stored encrypted and
  never shown again.
- **.env**, which wins over Settings and shows those fields locked there:

  ```sh
  LENS_PROMETHEUS=on
  LENS_PROMETHEUS_TOKEN=3f9c...   # openssl rand -hex 32
  ```

Until a token is set, `/metrics` answers 401 to everyone. While it is off, it answers 404.

## Where it is

`/metrics` is served by the web app's address, like the API (`http://<lens>:3000/metrics` on a default install), so it
follows `LENS_BIND`: on this machine only, unless Lens was opened to the network. A Prometheus on the same machine
scrapes it as it is; one elsewhere needs Lens reachable from it (or the remote access tunnel). The API answers on
`/metrics` itself too, for a scraper inside the Docker network (`http://backend:8000/metrics`).

## Scrape config

```yaml
scrape_configs:
  - job_name: lens
    scrape_interval: 60s
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/lens-token   # the scrape token, on one line
    static_configs:
      - targets: ["localhost:3000"]
```

Each scrape runs a few small queries on the database (grouped counts and sums), so once a minute is plenty; a
Raspberry Pi is happy at that rate.

## Grafana dashboard

[`grafana/lens-dashboard.json`](grafana/lens-dashboard.json) is the dashboard the monitoring profile loads, and a
starting point for a Grafana of your own: queue, running and failed jobs,
model cost over the last day, workers, disk; then jobs by status and pipeline, model calls, tokens, cost and failures
by model, budgets used, resources by status, imports and storage; and, from node-exporter, the machine's CPU, memory,
disk, load, network and uptime. In your own Grafana: **Dashboards → New → Import**, upload the
file and pick your Prometheus data source.

## What it measures

Everything is read from what Lens already keeps when Prometheus scrapes, so the numbers are the same ones the web app
shows. All are gauges.

| Metric | Labels | What it is |
| --- | --- | --- |
| `lens_build_info` | `version` | Always 1; the version running. |
| `lens_jobs` | `status` | Jobs by status: `queued` (the queue's depth), `running`, `held` (over budget), `succeeded`, `failed`, `cancelled`. |
| `lens_pipeline_jobs` | `pipeline`, `status` | The same, for jobs a pipeline queued. |
| `lens_routine_runs` | `status` | Routine runs kept, by status. |
| `lens_resources` | `status` | Resources (recordings and documents) by status; `error` counts failed imports and processing. |
| `lens_imports` | `window` | Resources imported in the last `1h` or `24h`. |
| `lens_activity_calls` | `window`, `kind`, `model` | Calls in the [activity ledger](activity.md): `in` (API changes), `out` (model calls, embeddings, webhooks…), `run` (jobs and routine runs ending). |
| `lens_activity_failed` | `window`, `kind`, `model` | Those that failed. |
| `lens_activity_seconds` | `window`, `kind`, `model` | Time spent in them. |
| `lens_activity_tokens` | `window`, `kind`, `model`, `direction` | Tokens in and out. |
| `lens_activity_cost_usd` | `window`, `kind`, `model` | Estimated cost, from the prices in Settings → Telemetry. |
| `lens_activity_unpriced` | `window`, `kind`, `model` | Priced calls whose cost is missing (no token counts): the cost shown is a floor. |
| `lens_budget_limit_usd`, `lens_budget_limit_tokens` | `resource`, `period` | Each [budget](budgets.md)'s caps. |
| `lens_budget_spent_usd`, `lens_budget_spent_tokens` | `resource`, `period` | What it spent this period. |
| `lens_budget_share` | `resource`, `period` | The largest share of its caps spent (1 is the cap). |
| `lens_budget_over` | `resource`, `period` | 1 when over its cap. |
| `lens_workers`, `lens_workers_up` | | Workers that have checked in; those seen in the last 2 minutes and not paused. |
| `lens_disk_total_bytes`, `lens_disk_free_bytes` | | The disk the data folder is on. |
| `lens_data_bytes` | | The data folder's size (database and files), measured in the background at most every 10 minutes. |
| `lens_scrape_errors`, `lens_scrape_seconds` | | Parts of the scrape that failed (see the API's log), and how long it took. |

`window` is `1h` or `24h`: the ledger's totals over the last hour and day, rather than counters, because the ledger
drops rows after `activity.keep_days` and a counter must never go down. For a month's spend, use the budgets.

## Privacy

Like telemetry, the metrics carry no personal content: no titles, file names, namespace names, prompts, answers,
people or addresses. Labels are statuses, ids (`routine:3`, a pipeline's id), model names and windows.

## Refine later

- Job step durations as histograms (from `step_stat`).
- Per-namespace breakdowns, opt-in, since namespace names are personal.
- The database's own size apart from files.
