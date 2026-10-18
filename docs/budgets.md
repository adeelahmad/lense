# Budgets

A budget caps what a routine, automation, pipeline, workflow, namespace or [scaling group](scaling.md) may cost. Budgets are off unless an admin sets one.
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

## One budget for everything

Besides budgets for single resources, `all` caps everything Lens spends: every model call, run and job, whatever it is
for. The setup wizard and the web installer ask for it after a cloud model (one on your own machine costs nothing), as a
scale for the hard limit with the soft limit at 80% of it; Settings › Budgets changes it.

```http
PUT /api/v1/setup/budget
{"hard_usd": 25, "soft_usd": 20, "period": "month", "on_over": "ask", "price_in": 3, "price_out": 15}
```

- **Soft limit**: past it, the budget is `near` and warns, as `warn_at` does (it is saved as `warn_at` = soft ÷ hard).
- **Hard limit**: at it, routines and jobs are held (or skipped, with `on_over: "skip"`) as any budget's are, and model
  calls that cost money stop with the reason until the limit goes up or the period turns. Models without a price
  (one on your own computer) keep working, unless the budget also caps `tokens`.
- `price_in`, `price_out`: the chat model's price in USD per million tokens (`ai.price_in`, `ai.price_out`), so
  what it spends is counted. Without a price a budget in USD counts nothing for that model.
- `hard_usd: null` removes the budget. `GET /api/v1/setup` shows it under `budget`. `PUT /api/v1/budgets?resource=all`
  sets it like any other budget, with `tokens` too.

## Before a run

Before a routine runs, an automation runs and a job's first step, Lens looks at the budgets of everything the run
counts for: the routine, the pipelines and workflows its actions name and its namespaces; the automation, its workflow
and its namespaces; a job's pipeline, namespace and the routine that queued it. It estimates the next run from the
average of that resource's runs in the last 30 days (always an estimate, shown with ≈). A run goes ahead unless a
budget is used up, or would be once the estimated run is done (for a `run` budget, unless the estimate alone passes
the cap). Then `on_over` decides:

- `ask` (default): the run is held. A routine or an automation gets a run with status `held`, a job the status
  `held`, each with `hold.why` saying where the budget stands. Someone picks: run it now, once, whatever the budget
  says, or skip it (`{"run": false}`):
  `POST /api/v1/routines/runs/{run}/decide {"run": true}`,
  `POST /api/v1/automations/runs/{run}/decide {"run": true}`,
  `POST /api/v1/jobs/{jid}/release {"run": true}`. Doing nothing changes nothing: a held run stays held, and a routine
  or automation that comes due again adds to the one held run (`hold.missed`) rather than piling up.
- `skip`: the run is skipped, and says why.
- `assistant`: the decision model (Settings › AI assistant, decisions) weighs it, with where the budget stands and the
  estimate. It runs only when the model is sure (`decisions.act_above`); otherwise it's held as with `ask`.

Asking a routine to run from the app or the API when it's over budget is refused with 409 and the reason; send
`{"over_budget": true}` to run it anyway.

## Warnings

Every five minutes the scheduler looks at every budget. One that crosses `warn_at` or its cap gets an alert, once per
period: on the budget (`alert`) and as a `budget.near` or `budget.over` row in its activity history. Automations
see it too, as a `budget.warned` activity ([Automations](automations.md#lens-itself-as-activities)).

## The meter and the Costs page

The top bar shows what the namespace picked in the switcher spent, against its budget: "$1.20 / $5" with a bar, gold
once the budget is near (`warn_at`) and red when it's used up. It counts the budget's own period (day, week or month);
without a budget it shows what was spent over the period you pick (day, week or month, remembered in this browser).
With "All namespaces" it shows what all of Lens spent (admins only), against the budget for all of Lens when one is set.
Clicking it opens the details: used and left, the spend for the period you pick and the models it went to.

**Costs** in the left menu has the rest for the same namespace and period: spend by day, by model, what cost most
(routines, pipelines, workflows, chats, documents), by namespace (for all of Lens), the namespace's budget to set or
change (admins), and every other budget with how much of it is used.

`GET /api/v1/budgets/meter?ns=&period=&detail=` serves both. Anyone who can read a namespace sees its meter; all of
Lens is admins'. A namespace's spend is the calls that count for it: its jobs, and requests to its own pages.

## Asking the assistant or an agent

Ask the assistant "where do my budgets stand?" or "what have I spent this month?" and it answers with the `budgets`
tool; agents call the same tool over MCP. It gives what each namespace you can read spent this period (its budget's
own day, week or month, else the month) against its budget, with what is left and whether it's ok, near or over. For
admins it adds all of Lens against the overall budget, and every routine's, automation's, pipeline's, workflow's and
scaling group's budget with where it stands. Figures are USD from the activity ledger, and `estimate: true` marks one
that's a floor because a priced call didn't say what it used. It reads only; setting a budget stays with admins in the
web app and `PUT /api/v1/budgets`.

## Refine later

Forecasting from a routine's schedule (its runs left this period), per-person budgets, budget warnings sent through
notifications and the weekly digest. The meter could
count chats in a namespace (they aren't attributed to one yet) and show a forecast to the end of the period.
