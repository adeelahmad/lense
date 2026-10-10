# Usage analytics

Lens works out how it is being used (active people, churn, retention, which features get used) on your own machine,
and can also send a few counted events to a **Google Analytics 4** property you own, to see the same across every
install you run.

Status: **built** (backend and API). The Settings page and the wizard toggle are **in progress**.

## The numbers

`GET /api/v1/admin/analytics?days=30` (admins) works these out from what Lens already keeps: the
[activity ledger](activity.md)'s rows for API calls a person made, and sign-ins in the audit log. Nothing new is
collected for them and nothing leaves the machine.

| Number | What it is |
| --- | --- |
| `daily` | each day of the period: how many people were active, and how many actions they took |
| `dau`, `wau`, `mau` | people active today, in the last 7 days, in the last 30 days |
| `stickiness` | average daily active people ÷ monthly active people (how much of the month a regular uses Lens) |
| `active_users`, `new_users`, `accounts` | people active in the period, accounts made in it, accounts not disabled |
| `churn` | of the people active in the period before this one (`previous_active`), how many came back (`retained`) and how many didn't (`churned`); `rate` = churned ÷ previous_active; `returning` were active now but not before |
| `retention` | for each of the last 8 weeks, the accounts made that week and the share of them active that week and each week after |
| `features` | each feature (from the API routes: recordings, chats, graph, routines…) with its actions and people |
| `last_active_day`, `days_since_active` | when anyone last used this install |

A person is active on a day when they changed something through the API or signed in. With `activity.reads` on,
reads count too. The ledger keeps `activity.keep_days` (365), which is as far back as the numbers can go.

## Google Analytics

**Off unless you turn it on.** It needs a GA4 property of your own: its **measurement id** (`G-…`) and a
**Measurement Protocol API secret** (in GA: Admin → Data streams → your stream → Measurement Protocol API secrets).

- **Settings → Usage analytics**: turn it on and set the id and secret. **Send a test event** sends one `lens_test`
  event to GA's validation server, which checks it without recording it.
- **The setup wizard**, on its telemetry step.
- **.env**: `LENS_ANALYTICS=on`, `LENS_GA_MEASUREMENT_ID=G-…`, `LENS_GA_API_SECRET=…`. What .env sets wins over
  Settings, which show those fields locked.

Turned on, one process (a worker, or `lens watch`) sends once a day, for the install as a whole:

| Event | When | Parameters |
| --- | --- | --- |
| `lens_install` | analytics first runs on this install | `version`, `platform` |
| `lens_setup_done` | the setup wizard was finished or skipped | `skipped` (0 or 1) |
| `lens_daily_active` | at least one person used Lens the day before | `active_users`, `actions` |
| `lens_feature_used` | each feature used the day before (up to 20) | `feature`, `actions`, `users` |
| `lens_upgrade` | Lens runs a new version | `from_version`, `to_version` |
| `lens_inactive` | nobody has used Lens for `analytics.inactive_days` (30) | `days` |
| `lens_return` | someone used Lens again after `lens_inactive` | `days_away` |

Every event also carries `session_id` (the day) and `engagement_time_msec`, so GA counts the install as an active
user that day, and the user properties `lens_version` and `lens_platform` (like `linux-x86_64`).

**What is sent:** the install's id (a random UUID made on first use, not derived from the machine), the version, the
platform and the counts above. **Never** a name, email, account id, title, file name, namespace, prompt or any text.
Like any web request, it reaches Google from your server's address (GA4 doesn't keep IP addresses). `analytics.events` chooses which events are sent; leave one out and it never is.

### Churn and retention in GA

Each install is one GA user. In GA:

- **Reports → Retention** shows installs coming back day after day and week after week, from `lens_daily_active`.
- **Churn rate** for a period is installs active in the period before that sent no `lens_daily_active` in it. An
  Exploration (Cohort exploration, with `lens_install` as the inclusion event and `lens_daily_active` as the return
  event) gives it by install week. `lens_inactive` counts installs that went quiet: a self-hosted install that was
  removed can't say so, so `inactive_days` without use is the uninstall-like signal.
- **Feature use**: register `feature` as an event-scoped custom dimension (Admin → Custom definitions), and `actions`
  and `active_users` as custom metrics, then break `lens_feature_used` down by `feature`.

## Settings

| Setting | Default | What it does |
| --- | --- | --- |
| `analytics.enabled` | `false` | send to Google Analytics |
| `analytics.measurement_id` | none | the GA4 measurement id, like `G-ABC123XYZ` |
| `analytics.api_secret` | none | the Measurement Protocol API secret (encrypted at rest, never shown again) |
| `analytics.region` | `global` | `eu` sends to `region1.google-analytics.com`, which keeps collection in the EU |
| `analytics.events` | all | which of the events above are sent |
| `analytics.inactive_days` | `30` | days without anyone using Lens before `lens_inactive` |

`GET /api/v1/settings/analytics/status` shows whether it is on, this install's id, which events are on, and how the
last send went. Changing these settings through the setup assistant always waits for an admin's approval, as
telemetry does.

## Refine later

- A Settings page with charts of the numbers, and the wizard toggle (in progress).
- Per-namespace numbers.
- Optional per-person pseudonymous ids in GA, for multi-person installs.
