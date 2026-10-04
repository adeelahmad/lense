# Budgets

A budget caps what a routine, pipeline, workflow or namespace may cost. Budgets are off unless an admin sets one.
What has been spent comes from the [activity ledger](activity.md), so a budget counts everything done for that
resource: a namespace's budget counts every job in it, and a routine's counts its runs and the jobs it queued.

## Setting one

```http
PUT /api/v1/budgets?resource=routine:3
{"usd": 5, "period": "month", "on_over": "ask"}
```

| Field | | |
| --- | --- | --- |
| `usd`, `tokens` | at least one | the cap in USD, tokens (in and out), or both |
| `period` | `month` | `run` (each run on its own), `day`, `week` (from Monday) or `month` (UTC) |
| `on_over` | `ask` | what happens to a run that would go over: see below |
| `warn_at` | `0.8` | the share spent at which the budget is `near` and gets a warning |

`GET /api/v1/budgets` lists every budget with where it stands, `GET /api/v1/budgets/status?resource=…` gives one
(also for a resource without a budget, with its estimated next run), and `DELETE /api/v1/budgets?resource=…`
removes one.

## Before a run

Before a routine runs and before a job's first step, Lens looks at the budgets of everything the run counts for: the
routine, the pipelines and workflows its actions name and its namespaces; a job's pipeline, namespace and the routine
that queued it. It estimates the next run from the average of that resource's runs in the last 30 days (always an
estimate, shown with ≈). A run goes ahead unless a budget is used up, or would be once the estimated run is done
(for a `run` budget, unless the estimate alone passes the cap). Then `on_over` decides:

- `ask` (default): the run is held. A routine gets a run with status `held`, a job the status `held`, each with
  `hold.why` saying where the budget stands. Someone picks: run it now, once, whatever the budget says
  (`POST /api/v1/routines/runs/{run}/decide {"run": true}`, `POST /api/v1/jobs/{jid}/release {"run": true}`), or skip
  it (`{"run": false}`). Doing nothing changes nothing: a held run stays held, and a routine that comes due again adds
  to the one held run (`hold.missed`) rather than piling up.
- `skip`: the run is skipped, and says why.
- `assistant`: the decision model (Settings › AI assistant, decisions) weighs it, with where the budget stands and the
  estimate. It runs only when the model is sure (`decisions.act_above`); otherwise it's held as with `ask`.

Asking a routine to run from the app or the API when it's over budget is refused with 409 and the reason; send
`{"over_budget": true}` to run it anyway.

## Warnings

Every five minutes the scheduler looks at every budget. One that crosses `warn_at` or its cap gets an alert, once per
period: on the budget (`alert`) and as a `budget.near` or `budget.over` row in its activity history.

## Refine later

Forecasting from a routine's schedule (its runs left this period), per-person budgets, budget warnings sent through
notifications and the weekly digest, and the assistant answering "where do my budgets stand?" in chat.
