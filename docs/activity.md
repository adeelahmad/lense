# Activity and costs

Lens keeps a ledger of what happens to each resource and what it cost: every request that changes something, every
call it makes out (a language model, embeddings, the decision model, text to speech, a webhook, a web tool), and every
job or routine run that ends. It is on by default and needs nothing set up.

## What a row holds

Each row names every resource it counts for, as `table:id`:

| Resource | Example |
| --- | --- |
| A routine and one of its runs | `routine:3`, `routine_run:12` |
| A job, its recording, namespace and pipeline | `job:40`, `recording:7`, `space:1`, `pipeline:2` |
| A workflow | `workflow:4` |
| A chat | `chat:9` |
| The person who asked | `account:1` |

A model call inside a workflow step of a job that a routine queued counts for the workflow, the job, the recording,
its namespace, its pipeline and the routine. A request counts for the resources in its path (`PATCH
/api/v1/routines/3` is `routine:3`), for what a `POST` made (from the `id` in its reply), and for who sent it.

Besides the resources, a row has its kind (`in`, `out` or `run`), what it was (`POST /api/v1/routines/{rid}`,
`model.chat`, `embeddings`, `decision`, `notify.webhook`, `tool.http`, `job.succeeded`, `routine.done`), the model,
tokens in and out, the estimated cost in USD, how long it took, and how it ended (an error's type or HTTP status,
never its message). It never holds prompt, reply, transcript or file content.

Cost comes from the prices you set: Settings › Telemetry › prices (USD per million tokens, per model), or the price
of the assistant's model under Settings › AI assistant. A model with no price counts tokens but no cost; local models
usually have none. `decisions.price_per_call` gives the decision model's price per decision.

A run row (a job or a routine run ending) repeats what its calls cost in all, and the job or run keeps it too
(`cost_usd`, `tokens`). Totals don't count run rows twice.

## Reading it

A resource's history merges its ledger rows with its audit log entries (kind `change`), newest first:

```http
GET /api/v1/activity?resource=routine:3&limit=100
GET /api/v1/activity?resource=routine:3&kind=out&before=2026-10-04T09:00:00.000+00:00
```

What it cost this day, week (from Monday) or month (UTC), or all time:

```http
GET /api/v1/activity/totals?resource=pipeline:2&period=month
GET /api/v1/activity/totals?period=day            # everything (admins)
GET /api/v1/activity/top?table=routine&period=week  # what cost most (admins)
```

Admins see every resource. Everyone else sees their own account's activity, their own chats', and the recordings and
namespaces they can read.

## Settings

| Setting | Default | |
| --- | --- | --- |
| `activity.enabled` | `true` | Off stops new rows (the audit log carries on). |
| `activity.reads` | `false` | Log API reads (GET) too. The web app reads often, so this grows the ledger fast. |
| `activity.keep_days` | `365` | Rows older than this are dropped, hourly, by the routines scheduler. |

Sign-in requests (`/api/v1/auth/`, `/api/v1/passkeys/`) aren't logged here; the audit log records sign-ins.

## Refine later

Compute time as a cost for local models (CPU seconds at a rate), per-person totals in the web app, and calls the
ledger doesn't see yet (Fedora sync, Matterbridge messages).
