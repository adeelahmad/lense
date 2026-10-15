# Automations

Automations are the workflow studio: Lens follows what happens in every source as a stream of time-coded activities,
and an automation watches that stream and the clock, tests its conditions, and acts (directly for routine things,
through the agent, or by asking you first). The timeline is the same stream, filtered and reviewed.

Status: in progress. The studio is built in modules; this page labels each part built or planned, and only what is
merged is called built.

| Part | Status |
| --- | --- |
| Match trees: one condition language for triggers, an automation's If and workflow nodes | built |
| Operators for times, lists, text patterns and changes (`in`, `matches`, `within`, `changed`...) | built |
| Activities, the kinds catalog, the event log, following the stream, timeline reads (`app/domain/events.py`) | built |
| New assets as `asset.added` (the `recordings` source) | built |
| More kinds: emails, files, calendar events, jobs, deletions, comments, notes, graph changes, sensors, chat | planned |
| The timeline API (`GET /api/v1/activities`), the MCP tools `timeline` and `event_kinds`, the assistant's `timeline` | built |
| Review marks on the timeline (reviewed, important, hidden), their filters and `mark_activities` ([Timeline](timeline.md)) | built |
| The shapes of triggers, joins, an If, policy and intents, checked (`app/domain/automation_spec.py`) | built |
| The action registry with risk from each action's effect, and one action, `log_line` (`app/domain/actions.py`) | built |
| Telling a person: the `notify` action and `channels.tell` (their own chat, a notification target, a Matterbridge room) | built |
| Settings → Automations: how often followers look and how long the event log is kept | built |
| Storing and running automations, schedules and timers, joins, the other actions, approvals, variables, the timeline page, the studio | planned |

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

| Kind | Parent | Source | Object |
| --- | --- | --- | --- |
| `asset.added` | | `recordings` | `{type: Audio, Video, Image or Document, id: lens:recording/<id>, name: its title, url: /resources/<id>, media: audio, video, image, document or transcript, content_type}` |

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
  `asset.added`. The actor is whoever added it: an automation (its first job was queued by `automation:<id>`), the
  storage source a watched file came from (`Service`, `lens:source/<id>`), the person whose upload it was (`Person`,
  `lens:account/<id>`), or Lens (a folder scan).
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
  (`object.from.0`); no path tests the value itself. The tests are in the table below.
- `all` holds when every condition in it does (an empty `all` always holds), `any` when one does (it needs at least
  one), `not` when its condition doesn't.
- Values are literals, never templates, so matching stays cheap and safe.
- A tree nests at most 6 deep and holds at most 50 tests. A problem is named with where it is: `the condition,
  all.1.not: compare with a number`.
- `match.describe(tree, kind=None)` reads a tree in words: `object.from ends with “@stmarys.org” and
  object.subject matches “trip” or “outing”`. With a kind, fields are named by the titles in its schema.

#### Operators

The first seven are the workflow engine's tests and work exactly as a condition node's always have. The others are
built in `app/domain/match.py` (`match.OPS` lists them all). When the value tested is a list, those pass when one of
its items does (as `contains` does), and `not_in` passes when none does. A value of the wrong kind, such as text for
`between` on numbers, doesn't pass; a test's own value is checked when it is saved, with the problem in words.

| Test | Value | Passes when the value tested |
| --- | --- | --- |
| `exists`, `empty` | none | is set; is missing, empty text, `[]` or `{}` |
| `equals`, `not_equals` | anything | is (or isn't) exactly the value |
| `contains` | anything | is text holding it in any case, or a list or object holding it |
| `gt`, `lt` | a number | is a number more (less) than it |
| `in`, `not_in` | a list of up to 500 values | is (or isn't) one of them; text in any case |
| `starts_with`, `ends_with` | text (a number is read as text) | is text, or a number, starting (ending) with it, in any case |
| `matches` | a pattern of up to 200 characters, such as `trip` or `^re:` | is text holding a match, in any case |
| `between` | two numbers, or two times: `[1, 5]`, `["-1d", "now"]` | is a number, or a time, from the first to the second (both included) |
| `before`, `after` | a time: `2026-10-16T07:30`, `now`, or from now: `2h`, `-30m` | is a time before (after) it |
| `within` | a duration: `30m`, `2h`, `1d` | is a time no further than that from now, either way |
| `weekday` | days: `["sat", "sun"]`, `["monday"]` or ISO numbers (1 is Monday) | is a time on one of those days |
| `time_between` | two times of day: `["22:00", "06:00"]` | is a time of day from the first up to the second, across midnight when the second is earlier |
| `changed` | none, or a path to keep values apart by: `actor.id` | differs from the value this test saw last time (the first one seen is no change) |

- **Times** are ISO 8601 times or dates. A time written without a zone (in the value or in what is tested) is in the
  automation's time zone, and so are `weekday` and `time_between`. Durations are a whole number and `s`, `m`, `h` or
  `d`; a minus points back (`-2h` is two hours ago).
- **Patterns** are regular expressions in Python's syntax, read in any case. To keep one pattern from tying up the
  worker on a crafted email, a pattern that repeats a part that repeats itself (`(a+)+`), repeats alternatives that
  may start alike (`(a|aa)+`) or refers back to a group (`\1`) is refused when it is saved, a pattern reads at most
  the first 20,000 characters of a value, and a match that takes over 50 ms counts as no match. Compiled patterns are
  cached.
- **`env`**: `match.test(tree, value, env)` reads time and state from `env`, never from globals, so tests and dry
  runs can fix them: `now` (the clock when not given), `timezone` (UTC when not given) and `state`.
- **`changed`** compares with the last value the same test saw for the same automation and trigger, kept in
  `automation_state` (kind `last`) by a `match.State(db, automation, alias, dry=False)` in `env["state"]`. Make one
  State per activity tested, so the If and a node testing the same thing agree. With `dry` it reads and keeps nothing.
  Without a State nothing has changed. With a path in `value` (`actor.id`), each sensor or sender keeps its own last
  value. Like every test, it is only reached when the tests before it in an `all` pass, so `changed` compares with
  the last value seen when it was reached.
- **`automation_state`** holds what automations keep between activities. Each automation keeps at most 1000 rows; past
  that the oldest go and are counted (`match.dropped(db, automation)`). A `last` value is kept 30 days after it was
  last seen; `match.tidy` drops expired rows once an hour, from the routine schedule.

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
(`actions.person`): an automation whose person was switched off or removed does nothing.

| Action | Does | Effect | Output |
| --- | --- | --- | --- |
| `log_line` | puts a line (`text`, `level` info or warning) in the run's log | read | `{line, level}` |
| `notify` | sends `text` (and a `link`; Lens's own paths become full links) `to` `me` (your own chat app), `target:<id>` (one of the namespace's [notification targets](notifications.md)) or `gateway:<name>` (a Matterbridge room) | self for `me`, else out | `{to, text, sent, failed}`: where it went |

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

## Refine later

- Variables inside match values (`{"var": "vars.threshold"}`), once saved variables are built.
- Statistical conditions, such as a reading above its 7-day average.
- The canvas shows and edits match trees on condition, filter and switch nodes (today it edits one test; a tree
  saved through the API is kept).
- A `?format=as2` JSON-LD view and per-actor outboxes for ActivityPub; SurrealDB `DEFINE EVENT` for Update verbs that
  no row records; pushing instead of polling.
- People given only some collections of a namespace see that namespace's activities about those collections.
- Name an automation actor by its automation's name once automations are stored.
- Telling a person: buttons on chat approvals (Telegram inline keyboards, Slack Block Kit); email as a `notify`
  channel; web push; private chats through Matterbridge (it can't tell them from rooms); with several people let in,
  "me" is whoever last wrote privately, until each chat user has a Lens account of their own; telling which links in a
  message the person's template wrote, so they stay when the run read outside text.
