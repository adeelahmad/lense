# Auto scaling

Auto scaling adds workers when there's work waiting and lets them go when there isn't, the way a cloud's auto
scaling groups do, within a budget you set. A **provider** is where units launch, a **scaling group** says what each
unit is, how many there may be and the rules that pick the number, and a **unit** is one machine or process the group
launched. Everything it does is in the [activity ledger](activity.md), and its cost counts against the group's
[budget](budgets.md).

Status: in progress. Built: groups, rules, approvals, budgets, the scaler, the API, MCP and assistant tools,
Settings › Auto scaling (the switch, and each group's terms to approve or pause), and **This machine**, a provider that
runs more `lens worker` processes where Lens runs. In progress: **DigitalOcean** and **Hetzner Cloud** providers are
built (credentials checked, list prices, launching, watching and deleting machines), and launch once the mesh's
install line, which another machine joins Lens with, lands ([the mesh](mesh.md)); so is **Proxmox VE**, clones of a
VM template on your own server. Planned: providers for AWS and any Linux host over SSH; model hosts (RunPod, Lambda, Hugging Face, SageMaker, Bedrock) for models; and making
providers and groups in Settings.

Auto scaling is **off** until an admin turns it on, and a group launches nothing until a person approves its terms
signed in to the web app. Agents can propose and manage groups, but never approve one.

## Turn it on

**Settings › Auto scaling** turns it on, or:

```http
PUT /api/v1/settings/scaling
{"enabled": true}
```

| Setting | Default | |
| --- | --- | --- |
| `enabled` | `false` | the master switch: off, every unit is let go and nothing launches |
| `tick_seconds` | `30` | how often the scaler looks (10 to 600) |
| `join_minutes` | `15` | how long a new unit may take to join before it counts as failed (1 to 120) |
| `accrue_minutes` | `5` | how often a running unit's cost goes into the ledger (1 to 60) |
| `tag` | `lens` | the prefix of every unit's name, so they can be told apart at the provider |

One process runs the scaler at a time: the API and every `lens worker` (except those started with `--steps` or
`--no-schedule`, as scaled workers are) take turns holding its lease. While scaling is off and nothing is left to let
go, the scaler doesn't even take the lease.

## Providers

```http
POST /api/v1/scaling/providers
{"name": "Here", "driver": "local"}
```

`GET /api/v1/scaling/drivers` lists the kinds of provider, with the settings each asks for (`fields`) and what a group
picks for each unit (`template`). A provider's credentials (`secrets`) are sealed when saved, bound to that provider
and field, and opened only for a call to it; the API shows only whether each one is set. Saving a provider proves its
credentials with a read-only call (`check`), kept on the provider; `POST /scaling/providers/{id}/check` runs it again.
A driver that needs a library fetches it the first time a provider of its kind is saved, so the default install
carries none of them.

| Driver | Units | Costs | Template |
| --- | --- | --- | --- |
| `local` (This machine) | more `lens worker` processes where the scaler runs | nothing | `steps`: the steps they run (none: every step) |
| `digitalocean` | droplets in `region`, with `token` (sealed), optional `ssh_keys` and `vpc_uuid` | DigitalOcean's hourly list price | `size` (`s-1vcpu-2gb`), `image` (`ubuntu-24-04-x64`), `steps` |
| `proxmox` (Proxmox VE) | clones of a VM template on `node`, at `url` with an API token (`token_id`, `token_secret` sealed); `verify_tls` (on) and `ca_cert` for a private CA | nothing, unless you give it a price | `vm_template` (its id), `full_clone` (off: linked), `storage`, `pool`, `steps` |
| `hetzner` (Hetzner Cloud) | servers in `location`, with `token` (sealed), optional `ssh_keys` | Hetzner's hourly gross price in euros, times `usd_per_eur` (1.1; set your rate) | `server_type` (`cx22`), `image` (`ubuntu-24.04`), `steps` |

A worker on This machine stops with the process that started it, and the scaler that takes over starts them again where
it runs.

### Machines elsewhere

A cloud provider's unit is a new machine. Its first boot runs a script (user-data) that sets `LENS_SCALING_UNIT` (its
name) and `LENS_WORKER_STEPS` (the template's steps), then the mesh's install line with a join token made for that
unit, usable once within an hour. The machine joins Lens over the [mesh](mesh.md) and starts `lens worker`; the
database credential travels inside the sealed join answer, so the user-data holds no long-lived secret. Each machine
carries Lens's tag (`scaling.tag`: a DigitalOcean tag, a Hetzner label `lens=<tag>`), so Lens's machines can be told
apart. Letting a unit go deletes its machine; a machine switched off at the provider counts as gone and is deleted.

A Proxmox unit is a clone of `vm_template`, tagged with Lens's tag and started. Its template needs the QEMU guest
agent (`qemu-guest-agent` installed, and the VM's Guest Agent option on): once the agent answers, Lens runs the same
first-boot script in the clone through it, then tags it `lens-booted`, so nothing is put on the server's storage. The
script stays in Lens's memory until then; a clone whose boot was cut short, by Lens restarting, never joins and is
deleted. Letting a unit go stops the clone and deletes it with its disks. The API token needs to clone, configure,
start, stop and delete VMs and use their guest agent (the `PVEVMAdmin` role on the template and the clones' pool is
enough).

Until the mesh is on and has its install line, a group on these providers waits (`waiting_for` says so) and launches
nothing, since its machines couldn't join. They call the providers' REST APIs with httpx, so they install nothing.

## Groups

```http
POST /api/v1/scaling/groups
{"name": "Transcribers", "provider": 1, "template": {"steps": ["transcribe"]}, "min": 0, "max": 4,
 "rules": [{"type": "step", "metric": "jobs_waiting", "above": 10, "minutes": 2, "add": 2},
           {"type": "step", "metric": "jobs_waiting", "below": 1, "minutes": 10, "remove": "50%"}]}
```

| Field | Default | |
| --- | --- | --- |
| `name`, `provider` | | |
| `template` | the driver's defaults | what each unit is |
| `min`, `max` | `0`, `1` | the bounds, up to 100 |
| `desired` | `min` | units wanted now; the rules change it |
| `cooldown_seconds` | `300` | after scaling out or in, how long before the next change the same way |
| `warmup_seconds` | `300` | how long a launching unit may take to join before more are judged |
| `terminate` | `oldest` | which goes first when scaling in: `oldest` or `newest`. Idle units go before busy ones, protected ones never |
| `rules` | none | up to 20, below |
| `budget` | none | USD a month; needed on a provider that charges |
| `at_cap` | `stop` | at the budget's cap: `stop` every unit, or keep `min` |
| `enabled` | `true` | off, its units are let go |

`PATCH /scaling/groups/{id}` changes one, `POST /scaling/groups/{id}/desired {"desired": 3}` asks for a number now
(within min and max), `POST /scaling/groups/{id}/pause {"paused": true, "why": "…"}` stops new launches while what runs
stays, and `DELETE /scaling/groups/{id}` lets its units go and removes it. `GET /scaling/groups/{id}/history` is what
happened to it, from the ledger. Each group says `waiting_for`: why it launches nothing now, in words.

### Rules

| Type | Fields | Proposes |
| --- | --- | --- |
| `target` | `metric`, `target` | units × metric ÷ target, rounded up: keeps the metric near the target |
| `step` | `metric`, `above` or `below`, `minutes` (default 1), `add` or `remove` (a count or a percentage like `"50%"`) | once the condition has held for `minutes`, that many more or fewer |
| `schedule` | `cron`, `timezone`, and any of `min`, `max`, `desired` | at each time the cron names, the new bounds and count |

When several rules speak, the largest number wins; the result stays within min and max. Each rule comes back with
`says`, the rule in words, and the group's `last_eval` keeps what each said last time.

| Metric | |
| --- | --- |
| `jobs_waiting` | jobs queued (for a group's `steps`, those that run them) |
| `oldest_wait_minutes` | how long the oldest queued job has waited |
| `workers_busy_share` | the share of live workers running a job |
| `worker_load` | the live workers' average load |
| `model_calls_per_minute` | model calls over the last 5 minutes |
| `model_seconds` | how long a model call took on average over the last 5 minutes |

`GET /api/v1/scaling/metrics` gives them now.

## The person approves

A group launches nothing until a person approves its **terms**: the provider, what each unit is, the max, the budget
and what happens at its cap. Settings › Auto scaling lists each group with its terms in words and an **Approve**
button; `POST /scaling/groups/{id}/approve` approves them as they stand, and only from a session signed in to the web
app: an API key, an app or an agent gets 403. A group made or changed by a person in the web app
is approved as it's saved, since that is their pick.

Within the approved terms anyone who may change the group, agents included, acts at once: asking for a number of units,
lowering the max or the budget, pausing, letting a unit go. A change that widens the terms (another provider or unit,
a higher max or budget, keeping min at the cap) takes the approval away until a person gives it again.

## The scaler

Every `tick_seconds`, for each group, the scaler:

1. puts each running unit's cost since last time into the ledger (`scaling.unit`, an estimate from its price per hour);
2. checks the group's budget and the `all` budget: at the cap the group is `capped` and lets its units go (or keeps
   min, with `at_cap: "min"`);
3. looks at each unit at its provider and in Lens. A worker unit is running once its worker beats; one that hasn't
   joined within `join_minutes`, or whose worker has been silent for 10 minutes, fails and is let go;
4. runs the rules and launches or lets go units to reach the desired count. A busy worker drains first: its job goes
   back to the queue after the step it's on, and the unit goes once it's idle.

Three launches or joins that fail in a row pause the group, with the reason. Every launch, join, drain, termination,
failure and change is a ledger row linked to the unit, its group and its provider.

## Agents

The same in the assistant (admins, in the server tools) and over MCP: `scaling` reads everything,
`propose_scaling_group` and `change_scaling_group` make and change groups, `set_scaling_desired`,
`pause_scaling_group` and `terminate_scaling_unit` act within the approved terms. In the assistant, proposing or
changing a group waits for the person's yes; a yes on the Approvals page in the web app also approves the group's
terms, while a group the assistant makes in a setup conversation, or an agent over MCP, still waits for the person.
Turning scaling on (`change_settings scaling`) always asks.

## Refine later

Providers for SSH hosts and model hosts; Proxmox LXC containers; the AWS provider (in its own pull request); revoking a unit's mesh
certificate when it goes; reporting machines with Lens's tag that Lens has no unit for (`listing`); a live euro
rate; spot and preemptible instances; warm pools; predictive scaling; health checks beyond
joining; prices fetched from providers' pricing APIs.
