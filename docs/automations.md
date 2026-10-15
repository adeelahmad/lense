# Automations

Automations are the workflow studio: Lens follows what happens in every source as a stream of time-coded activities,
and an automation watches that stream and the clock, tests its conditions, and acts (directly for routine things,
through the agent, or by asking you first). The timeline is the same stream, filtered and reviewed.

Status: in progress. The studio is built in modules; this page labels each part built or planned, and only what is
merged is called built.

| Part | Status |
| --- | --- |
| Match trees: one condition language for triggers, an automation's If and workflow nodes | built |
| Activities, the kinds catalog, the event log, following the stream, timeline reads (`app/domain/events.py`) | built |
| New assets as `asset.added` (the `recordings` source) | built |
| New assets by kind: emails received, calendar events, files from storage, uploads, pages saved from the web, imports | built |
| Runs that finished or failed, deletions and calendar events that moved | built |
| More kinds: comments, notes, graph changes, sensors, chat | planned |
| The timeline API (`GET /api/v1/activities`), the MCP tools `timeline` and `event_kinds`, the assistant's `timeline` | built |
| Review marks on the timeline (reviewed, important, hidden), their filters and `mark_activities` ([Timeline](timeline.md)) | built |
| The shapes of triggers, joins, an If, policy and intents, checked (`app/domain/automation_spec.py`) | built |
| The action registry with risk from each action's effect, and one action, `log_line` (`app/domain/actions.py`) | built |
| Telling a person: the `notify` action and `channels.tell` (their own chat, a notification target, a Matterbridge room) | built |
| The model and the agent: the `ask_model`, `call_tool` and `ask_agent` actions, and prompts with outside text marked | built |
| Calling a web address: the `http_request` action, with credentials in headers kept sealed | built |
| Settings → Automations: how often followers look and how long the event log is kept | built |
| The Timeline page with filters, grouping, review keys and new items, and Home's Latest card ([Timeline](timeline.md)) | built |
| Storing and running automations, schedules and timers, joins, the other actions, approvals, variables, the studio | planned |

## Contract

Every part of the studio builds on the shapes below. A change to one of them comes in a small pull request of its
own that changes the code, this section and the tests together.

### Activities

Built (`app/domain/events.py`). Everything that happens in Lens, from every source, is an activity: a plain dict in
the shape of a [W3C Activity Streams 2.0](https://www.w3.org/TR/activitystreams-core/) activity, with a Lens `kind`
that triggers subscribe to. `events.check_activity(a)` checks it, naming the first problem.

```json
{"id": "create-recording-7", "type": "Create", "kind": "asset.added", "published": "2026-10-15T08:12:03.000Z",
 "actor": {"type": "Person", "id": "lens:account/1", "name": "Sam"},
 "object": {"type": "Audio", "id": "lens:recording/7", "name": "Call with the school", "url": "/resources/7",
            "media": "audio"},
 "target": null, "tag": [], "summary": "Sam added “Call with the school”", "space": 1,
 "lens": {"source": "recordings", "pos": ["2026-10-15T08:12:03+00:00", 7], "untrusted": ["object.name"],
          "cause": null}}
```

- `id`: stable, made from what the source reads (`create-recording-7`, `log-12`), so an activity is the same every
  time it is read: 1 to 120 lowercase letters, digits and `_ . : -`.
- `type`: the AS2 verb (Create, Update, Delete, Add, Remove, Join, Leave, Arrive, Announce, Like, Accept, Reject),
  which the kind decides.
- `published`: when it happened, ISO 8601 in UTC with milliseconds, so times from every source sort as text.
- `actor`: who did it, `{type, id, name}`. Sources and storage are `Service`, people `Person`, Lens itself, the agent
  and automations `Application` (`lens:automation/4`; Lens is `lens:app`). Entities are never actors; they go in
  `tag`.
- `object`: what it is about, with the kind's own fields. Ids are `lens:<table>/<id>` references, so actions can act
  on them; `events.ref_id(ref, "recording")` reads the id back.
- `target`: where to, when the verb has one (Add to a collection), else null. `tag`: up to 50 objects such as
  `{"type": "Mention", "id": "lens:entity/9", "name": "St Mary's"}`.
- `summary`: one line of at most 300 characters, made by fixed rules, never by a model.
- `space`: the namespace id, or null for activities of no namespace.
- `lens`: Lens's own fields. `source` and `pos` (the source's position, `[time, id]`) are for the stream machinery.
  `untrusted` lists the dotted paths holding outside text (titles, email bodies, webhook payloads): prompts wrap
  them with `untrusted.wrap`. `cause` is `{automation, run, depth}` when an automation's action caused it. `spaces`
  lists every namespace when there are several. `audience` narrows who may see it: `space` (the default: people who
  read one of its namespaces), `owner` (only the account in `lens.owner`) or `admins`.

### Kinds

Built. `events.kind(name, *, label, source, as_type, object_type, parent=None, fields=None, untrusted=(),
sample=None, noisy=False)` registers a kind: a name like `noun.past_verb`, a label for people, the source that makes
it, its AS2 verb and object type, a parent (a trigger on the parent matches it too), JSON Schemas for its `object`
(and `actor` or `target`) in `fields`, the paths that hold outside text, a realistic sample (checked when it is
registered), and whether it is `noisy` (left out of the timeline unless named). `events.kinds()` lists them,
`events.kind_info(name)` gives one with its children and its whole `schema` (every activity's fields, its parents',
then its own), `events.descendants(name)` the kind and every kind under it, and `events.field_at(kind, path)` the
schema of one field, whose `title` names it in words (`match.describe(tree, kind)` says "the title contains “call”").

Every kind's object has `type`, `id`, `name` and `url`; the table lists what each adds. Its fields' titles, a sample
and the paths holding outside text are in `GET /api/v1/activities/kinds` (`event_kinds` over MCP).

| Kind | Parent | Source | Object | Example summary |
| --- | --- | --- | --- | --- |
| `asset.added` | | `recordings` | `{type: Audio, Video, Image or Document, id: lens:recording/<id>, name: its title, url: /resources/<id>, media: audio, video, image, document or transcript, content_type}` | Lens added “Scan 12” |
| `email.received` | `asset.added` | `recordings` | `from`, `to`, `cc` (addresses), `subject`, `list`, `mailbox`, `date` (sent), `snippet` (the start of its text), `attachments` (how many) | Work mail received “School trip on Friday” from office@stmarys.org |
| `calendar_event.added` | `asset.added` | `recordings` | `startTime`, `endTime`, `location`, `organizer`, `attendees` (addresses), `calendar` | Family calendar added the event “Parents' evening” on 2026-10-20 |
| `file.added` | `asset.added` | `recordings` | `path` (in its source), `mediaType`, `size` (bytes), `duration` (seconds) | Office share added “Minutes” |
| `upload.added` | `asset.added` | `recordings` | `path` (the file's name), `mediaType`, `size`, `duration` | Sam uploaded “Call with the school” |
| `web_page.saved` | `asset.added` | `recordings` | `href` (the page's address), `site` | Sam saved “Museum opening hours” from museum.example.org |
| `import.received` | `asset.added` | `recordings` | `via`: hook (an import hook), paste (pasted text) or file (a file read as text) | Phone shortcut imported “Shopping list” |
| `asset.processed` | | `jobs` | the asset's fields, `job` (`lens:job/<id>`), `steps`, `batch`; tagged with its entities | “School trip on Friday” finished processing (pages, analyze, embed) |
| `asset.failed` | | `jobs` | as `asset.processed`, with `step` (where it failed) and `error` | Processing “Call with the school” failed at transcribe |
| `asset.deleted` | | `log` | `type`, `id`, `name`, `media` | Sam deleted “Old minutes” |
| `calendar_event.changed` | | `log` | `startTime`, `endTime`, `location`, `calendar`, and `before` (`startTime`, `endTime`) | Family calendar moved “Parents' evening” to 2026-10-21 18:00 |

A new asset is one activity, of the most specific kind its row tells, decided by its content type and where it came
from, never by reading its file: an email (content type email, or from an IMAP or Gmail mailbox), a calendar event
(content type calendar_event, or from an iCal feed or Google Calendar), an import hook's, one saved from the web
(webcapture.py, mediaurl.py), a file from a storage source or Drive, an upload, an import (pasted text, a transcript
imported), else `asset.added`. A file an email or archive held, made a resource of its own, stays `asset.added`: the
email is what arrived. A trigger on `asset.added` matches every one of them.

A run of an asset's pipeline that finishes is `asset.processed` (it succeeded) or `asset.failed`, one activity per
run, batch runs included. Its actor is whoever queued it, and its tags are the asset's entities (the ten most
mentioned), which exist only once it has been processed: `asset.processed` is the kind for "when an email about St
Mary's is understood". A run tried again finishes again, as another activity. A deletion is logged as
`asset.deleted` where the asset is deleted (once per asset); a calendar event read again that starts at another time
is logged as `calendar_event.changed` (once per event and new start), with the times it had before.

Fields come from what the row keeps, so reading them costs nothing on the ingest path. A message's addressing is
kept as it is listed (`recording.mail`: who sent it to whom, its subject, list, mailbox, and Gmail's snippet), an
event's times, place and people as it is imported (`recording.event`), and an email's attachments and the start of
its text once it has been read (`recording.email`). Emails and events imported before these were kept have only the
fields their rows had. Outside text (titles, subjects, addresses, snippets, places, file names, page addresses) is
listed in `lens.untrusted`.

### Sources and the event log

Built. A source (`events.Source`, added with `events.register_source`) reads rows Lens already keeps as activities,
so nothing is stored twice:

- `read(db, cfg, pos, spaces, limit, hint)`: activities strictly after `pos` (`[time, id]`, or `[time, null]` for
  after every row at that time), oldest first, at most `limit`, of the namespaces in `spaces` (null: all). `hint`
  is narrowing a source may use.
- `page(db, cfg, before, viewer, flt, limit)`: newest first, strictly before `before` (`key` of the last one shown),
  with what the viewer may see and the filters it can apply cheaply (the stream checks every filter again).
- `get(db, cfg, activity_id)`: one activity by its id, or null; `now(db)`, `back(db, seconds)`: positions of now and of
  `seconds` ago; `key(a)`: the position `page` orders by.

A kind belongs to the source named in its `source`, and a source may list more in `kinds`. Two are built:

- `recordings` (`app/domain/events_content.py`) reads `recording` by `created_at`, the id breaking ties, as
  `asset.added` and its children (Kinds, above). The actor is whoever added it: an automation (its first job was
  queued by `automation:<id>`), the storage source or mailbox it came from (`Service`, `lens:source/<id>`), an import
  hook (`Service`, `lens:import_hook/<id>`), the person whose upload, clip or import it was (`Person`,
  `lens:account/<id>`, or `lens:email/<address>` for someone who isn't an account), a sensor's digest (`Service`,
  `lens:sensor/<id>`), or Lens (a folder scan). `events_content.actors(db, labels)` names them the way Lens writes
  them down (an account id, an email, `watch:<id>`, `routine:<id>`, `import_hook:<id>`, `automation:<id>`, ...),
  in a few queries for a whole page, and keeps names for 30 seconds.
- `jobs` (`app/domain/events_content.py`) reads `job` by `finished_at`, the id breaking ties, as `asset.processed`
  and `asset.failed`; their ids carry the time they finished (`processed-job-51-1792081450`), so a run tried again
  is a new activity.
- `log` reads `event_log`: activities no row of their own records (a deletion, a webhook call), written by
  `events.emit(db, kind, *, actor, obj, space, summary, target=None, tag=None, key=None, published=None, spaces=None,
  audience=None, owner=None, untrusted=None)`, which gives the id (`log-<n>`). Emitting a `key` again changes
  nothing and gives the first id, so a retry never doubles an activity. Inside `with events.caused_by({"automation":
  4, "run": 51, "depth": 1}):` what is emitted carries that cause. It is read by `(logged_at, id)` and paged by
  `published`, and its rows are kept for `events.keep_days` (30; Settings → Automations).

### Following the stream

Built. The API and `lens worker` are separate processes on one database, so following works the way notifications
do: `events.follow(db, cfg, consumer, kinds=None, spaces=None, limit=500, hint=None)` gives `(activities, commit)`,
the activities since the consumer's last commit, oldest first. Handle them in order, then call `commit()` to save
where the next look starts (`event_cursor:<consumer>`, a position per source).

- A consumer seen for the first time starts at now: nothing that happened before replays, not even through the
  lookback. What happens in the second it starts in isn't seen.
- Only `kinds` (and their descendants; null: every kind) in `spaces` (null: every namespace and none). A source that
  makes none of the kinds wanted isn't read, and its cursor moves to now, so a kind listened to later doesn't replay
  a backlog. `events.idle(db, consumer)` moves every cursor to now.
- A source with more waiting gives `limit` at a time. Once caught up, a look also reads the last 60 seconds again,
  for a row written as the last look ran, minus what this process already handled. So claim each activity before
  acting on it: `events.claim(db, consumer, key)` is true for the one process, and the one time, that claims `key`
  (an activity's id, say), with a `CREATE` of `event_claim:<hash>` only one process wins. `events.unclaimed(db,
  consumer, keys)` lists the keys nobody has claimed yet, in one query. Claims go after a day.
- `events.emit` wakes the followers in its process; a follower waits between looks with `g = events.generation()`
  before the look and `events.wait(events.poll_seconds(cfg), stop, since=g)` after it (`events.poll_seconds`, 5;
  Settings → Automations).

### Timeline reads

Built. `events.page(db, cfg, viewer, before=None, flt=None, limit=50)` reads the stream newest first for a viewer
(`events.viewer(account, spaces, admin)`, spaces null for every one): `{"items": [...], "next": ...}`, where `next`
(an opaque cursor, null at the end) goes in `before` for the page after. It merges every source's `page`, applies
who may see what, and when filters thin a page out it reads on, up to five pages from each source.
`events.get(db, cfg, id, viewer)` reads one activity, null when there is none or it isn't the viewer's to see.

- Who sees what: `lens.audience` `owner` is its owner alone; `admins` is admins; `space` is everyone who reads one of
  its namespaces (`space` or `lens.spaces`); admins read every namespace, and an activity of no namespace is for
  admins. People who were given only some collections of a namespace don't see its activities yet.
- Filters: `kinds` (with their descendants; a noisy kind only when named), `actor` (an actor's id, or several),
  `spaces` (namespace ids), `tag` (an entity's id or name), `since` (published after) and `until` (at or before),
  `q` (words in the summary) and `where` (a match tree on the activity, below).
- `events.tidy(db, cfg)` drops event log rows past `events.keep_days` and claims past a day; the routine scheduler
  runs it once an hour.

### Match trees

Built (`app/domain/match.py`). One condition language for a trigger's `where`, an automation's `if`, and the
condition, filter and switch nodes of a workflow:

```
M := {"all": [M, …]} | {"any": [M, …]} | {"not": M} | {"path": "a.b.0", "op": OP, "value": V}
```

- A test (`{path, op, value}`) reads the part of the value at a dotted path, list items by number
  (`object.from.0`); no path tests the value itself. The tests are the workflow engine's: `exists`, `empty`,
  `equals`, `not_equals`, `contains` (text in any case, or an item of a list), `gt` and `lt` (numbers).
- `all` holds when every condition in it does (an empty `all` always holds), `any` when one does (it needs at least
  one), `not` when its condition doesn't.
- Values are literals, never templates, so matching stays cheap and safe.
- A tree nests at most 6 deep and holds at most 50 tests. A problem is named with where it is: `the condition,
  all.1.not: compare with a number`.
- `match.describe(tree)` reads a tree in words: `object.from contains “@stmarys.org” and (object.subject contains
  “trip” or object.subject contains “outing”)`.

In a workflow, a condition, filter or switch case takes either one test, as before, or a tree in `match`; with a
tree, `path` (when given) is the part of the value the tree tests. Graphs saved before keep running unchanged.

```json
{"id": "school", "type": "condition",
 "config": {"path": "object", "match": {"all": [{"path": "from.0", "op": "contains", "value": "@stmarys.org"},
                                                {"not": {"path": "attachments", "op": "gt", "value": 5}}]}}}
```

### Automations: triggers and joins

Built as shapes with checks (`app/domain/automation_spec.py`); storing and running automations is planned. Each check
gives a cleaned copy, or a ValueError naming the first problem.

```json
{"name": "School emails", "enabled": true, "spaces": [3], "run_as": 1,
 "triggers": [{"as": "mail", "on": "event", "kinds": ["email.received"],
               "where": {"all": [{"path": "object.from.0", "op": "contains", "value": "@stmarys.org"}]}},
              {"as": "morning", "on": "schedule", "cron": "0 7 * * 1-5", "timezone": "Europe/London"}],
 "join": {"mode": "any"},
 "if": {"all": [{"path": "events.mail.object.subject", "op": "contains", "value": "trip"}]},
 "workflow": 12, "policy": {"n7": "ask"}}
```

- `check_trigger(t)`, `check_triggers(list)`: one to ten triggers, each named once in `as` (lowercase letters,
  digits and `_`). `on` is `event` (`kinds`, an optional `where` match tree, `from_automations`, false by default so
  activities made by automations are left out), `schedule` (`cron`, five fields as routines take them, and
  `timezone`), `at` (one moment, in `timezone`), `relative` (a time `field` of a matching activity plus an
  `offset`, which may point back: `-30m`), `webhook` (`hook`, given when the automation is saved) or `manual`.
- `check_join(j, aliases)`: how the triggers make a run. `any` (the default): each on its own. `all`: every trigger
  `within` a time, optionally on the same `key` path. `count`: `at_least` n of trigger `of` `within`. `absent`:
  after trigger `after`, trigger `missing` not seen `within`. `batch`: collect what matches `within`, then one run.
- `check_duration(text, back=False)`: seconds in `30s`, `30m`, `2h` or `7d` (at most a year); a minus only where an
  offset may point back.
- `check_if(tree)`: the If is a match tree over the run context, so its paths start with one of the context's keys.
- `check_policy(p, nodes, allow_auto=False)`: `{action node id: ask or auto}`. Only a person in a signed-in session
  lets a node run on its own; everywhere else (agents, drafts, the workflow API) `auto` is refused.

### Run context

What an automation's If, its nodes and its templates see (`automation_spec.context_paths()` lists the keys, with
titles, for the variable picker):

| Key | Holds |
| --- | --- |
| `event` | the activity that started the run (for a join, the last one) |
| `events` | each trigger's activity by its name, such as `events.mail` |
| `trigger` | the trigger that fired: `{as, on}` |
| `automation` | `{id, name, version, spaces}` |
| `run` | `{id, dry}` |
| `namespace` | `{id, name}` |
| `user` | who the automation acts for: `{id, name, email}` |
| `now`, `timezone` | when the run started (ISO 8601 in UTC) and the person's time zone |
| `vars` | the automation's and its namespace's saved variables |
| `steps` | what each node passed on so far, by node id, such as `steps.n3` |

### Actions and intents

Built: the registry (`app/domain/actions.py`). An action is registered with `actions.register(ActionSpec(name,
label, group, params, perform, required=(), effect="read", risk=None, output=None, describe=None, subject=None))`:
`params` is a JSON Schema of type object, `perform(db, cfg, intent, user, dry)` carries it out and gives a dict (with
`dry`, it says what it would do and changes nothing). `actions.catalog()` lists every action for the builder and
agents, `actions.get(name)` gives one, `actions.check_params(spec, params)` checks params and fills in defaults (a
string holding `{{ }}` stands for any type, since it is filled in when the action runs), and `actions.perform(db,
cfg, intent, user, dry=False)` checks an intent and carries it out.

When an action node runs, it makes an **intent**, checked by `automation_spec.check_intent`:

```json
{"key": "51-n7", "action": "notify", "params": {"to": "me", "text": "School trip on Friday"}, "effect": "self",
 "risk": "routine", "untrusted": true, "spaces": [3], "run_as": 1, "node": "n7", "automation": 4, "run": 51,
 "when": null, "status": "waiting"}
```

Its status is `waiting`, `scheduled`, `done`, `failed`, `declined` or `expired`. The action's effect decides the
risk (`actions.resolve_risk(spec, params, ctx, policy)`, where `policy` is the node's pick or the automation's whole
policy with the node in `ctx["node"]`):

| Effect | Means | Risk |
| --- | --- | --- |
| `read` | looks things up, changes nothing (a tool asks for any change itself) | routine |
| `self` | tells or keeps something for the automation's own person only | routine |
| `lens` | changes something inside Lens | routine on the event's own object in the automation's namespaces, else ask |
| `out` | sends outside Lens, reaches other people, or deletes | ask, unless the person set the node to `auto` |

An effect may be worked out from the params (`notify` to "me" is `self`, to a room is `out`); when it can't be
worked out, it counts as `out`. An action's own `risk` function may make it stricter, never looser.

An action acts for the automation's person (`run_as`), with their permissions as they are when it runs
(`actions.person`), within the automation's namespaces (`actions.reach`): an automation whose person was switched off
or removed does nothing. What it writes is by the automation itself, `automation:<id>` (`actions.actor`).

| Action | Does | Effect | Output |
| --- | --- | --- | --- |
| `log_line` | puts a line (`text`, `level` info or warning) in the run's log | read | `{line, level}` |
| `notify` | sends `text` (and a `link`; Lens's own paths become full links) `to` `me` (your own chat app), `target:<id>` (one of the namespace's [notification targets](notifications.md)) or `gateway:<name>` (a Matterbridge room) | self for `me`, else out | `{to, text, sent, failed}`: where it went |
| `ask_model` | asks the model a `prompt` (with your own `system` instructions and `model` if you like); with `json_schema` (a JSON Schema of type object) the reply is that object | read | `{text}`, or the object the schema describes |
| `call_tool` | calls one of the assistant's tools (`tool`, with `args`) as the automation | read: a change becomes an approval | `{tool, result, summary, approvals}` |
| `ask_agent` | hands what happened (`about`, by default the event) to the agent with your `instructions`, limited to some `tools` if you like, for up to `max_steps` rounds (6, at most 12) | read: changes become approvals | `{answer, chat, approvals, steps}` |
| `http_request` | sends a `method` (GET, POST or PUT) to a `url`, with `headers` and a `body` (text, or an object sent as JSON) | out | `{status, ok, body, json, content_type, truncated, method, url, headers}`: the answer, as outside text |

#### Telling a person

`notify` to `me` goes through `channels.tell(db, cfg, account, text, links=())`: every chat channel that answers as
your account (the Telegram bot, the Slack app) keeps the private chat it last had with someone on its list of people
who may talk to Lens (`chat_route:<channel>`, updated as it answers), and the message goes there. A group or a room is
never used, since it reaches other people, and neither is Matterbridge, where a gateway is a room. With no such chat
the action fails with "no chat app linked", saying how to link one. The result names each channel and chat it used,
so a reply there can be matched to what was sent.

Chat apps fetch what a message links to show a preview, so a web address built from what the run read would carry it
out before anyone clicks. When the run read text from outside Lens (the intent's `untrusted`), web addresses in the
message are broken up (`hxxps://evil[.]example/...`), except Lens's own; when it read none, the person wrote them and
they stay. The same goes for notification targets and Matterbridge rooms. A notification target must be in one of the
automation's namespaces, switched on, and the automation's person must be able to edit that namespace; a Matterbridge
room needs the bridge on and answering as the automation's person (or the person to be an admin).

#### The model and the agent

`ask_model`, `call_tool` and `ask_agent` use the assistant's tools as the automation, with its person's permissions
within the automation's namespaces, their extensions and no server tools. Nobody reads along while an automation runs,
so every change asks the person: a tool that changes something makes an approval (notes included, which the assistant
in chat writes at once), and a tool that could carry data out of Lens (an extension's web or server tool) asks first
whatever the run read. The approvals are the person's (`GET /api/v1/approvals`), with the run as their `origin`
(`{automation, run, node}`); the output lists their ids, so a later node can tell you what waits.

`ask_agent` saves the exchange as one of your conversations, titled "<automation>: <what happened>", with the
activity's page as what it was asked from, so you can open it, approve what it proposed there and carry on. Model calls
count in the activity ledger under the run, and stop once a budget they count for (the namespace's, or everything's)
is used up.

Params marked `"format": "prompt"` in an action's schema (`ask_model`'s `prompt`, `ask_agent`'s `instructions`) are
prompts: they are rendered with `actions.render_prompt(text, ctx, cfg)`, which marks as data (`<untrusted-…>`) what
the run read from outside Lens before the model sees it: each activity's `lens.untrusted` fields (an email's subject
and body) and everything earlier steps passed on (a model's reply, a web page). Your own words in the prompt stay as
written. `ask_agent` passes the activity it is about the same way.

#### Calling a web address

`http_request` reaches public addresses only, or networks an admin allowed for notifications (Settings →
Notifications), connecting to the address it checked and following no redirect (a 3xx is passed on as it is). It takes
10 seconds at most, sends a body of up to 64 KB, and passes on the status and the first 64 KB of the answer (`json`
too when the answer is JSON and whole). The answer is outside text: a later prompt reads it marked as data. It always
reaches outside Lens, so it asks first unless you set its node to run on its own; each call goes in the activity
ledger (`automation.http`, with the method, host and status).

Credentials go in headers. A header whose name says it carries one (`Authorization`, cookies, API keys, tokens,
secrets, passwords, signatures) is never shown: what the action describes and passes on says `••••`. When an automation
is saved, `actions_web.seal_headers(db, cfg, space, automation, url, headers)` seals those values with the namespace's
key ([Encryption](encryption.md)), bound to the automation and to the scheme, host and port of its address, so a
sealed value can't be copied into another automation or sent to another host; it is opened only to send. A locked
vault stops the request until it is unlocked.

## Refine later

- More operators: `in`, `matches` (bounded regular expressions), `between`, times against now, `changed`.
- The canvas shows and edits match trees on condition, filter and switch nodes (today it edits one test; a tree
  saved through the API is kept).
- A `?format=as2` JSON-LD view and per-actor outboxes for ActivityPub; SurrealDB `DEFINE EVENT` for Update verbs that
  no row records; pushing instead of polling.
- People given only some collections of a namespace see that namespace's activities about those collections.
- Name an automation actor by its automation's name once automations are stored.
- A timeline page filtered to one child of `asset.added` (only emails, say) reads every new asset and keeps those of
  that kind, up to five source pages; narrow it in the query (path, `remote`, `web`) if that proves slow.
- Moves of Google Calendar and iCal events only: events in a calendar file on storage are read as new files. An
  event that was cancelled or deleted from its calendar isn't an activity yet.
- Folder scans of a namespace's own folders as `file.added`; uploaded `.ics` files with their event's fields; a
  snippet for IMAP messages before they are read (it needs the message body).
- Per-recipient threading of email conversations; entity tags on new assets once extraction runs at ingest.
- Telling a person: buttons on chat approvals (Telegram inline keyboards, Slack Block Kit); email as a `notify`
  channel; web push; private chats through Matterbridge (it can't tell them from rooms); with several people let in,
  "me" is whoever last wrote privately, until each chat user has a Lens account of their own; telling which links in a
  message the person's template wrote, so they stay when the run read outside text.
- The model and the agent: per-node model picks in the studio; streaming an agent's steps into the run's log as they
  happen; a namespace assistant's memory for automations (`remember` is left out today); approving an agent's
  proposals from the chat app it told you in; count the automation's own budget once budgets take `automation:<id>`.
- Calling a web address: secrets in the address itself (a Slack or Discord webhook URL) sealed like headers; PATCH and
  DELETE (DELETE would count as deleting); OAuth sign-in for an API; form-encoded bodies; retries with backoff;
  networks allowed for automations apart from notifications.
