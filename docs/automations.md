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
| Comments, notes written and saved, and changes to the entity graph | built |
| Sensors: readings a sensor keeps, alert and notable log patterns, devices that report for the first time ([Sensors](#sensors-as-activities)) | built |
| What people say to Lens, in the web app and chat apps, as `chat.message` ([Chat](#chat-as-activities)) | built |
| Lens itself: routine runs, budget warnings, backups and new releases ([Lens itself](#lens-itself-as-activities)) | built |
| The timeline API (`GET /api/v1/activities`), the MCP tools `timeline` and `event_kinds`, the assistant's `timeline` | built |
| Review marks on the timeline (reviewed, important, hidden), their filters and `mark_activities` ([Timeline](timeline.md)) | built |
| The shapes of triggers, joins, an If, policy and intents, checked (`app/domain/automation_spec.py`) | built |
| The event workflow scope: what an automation does, drawn with action nodes and tried on an activity (`app/domain/automation_nodes.py`) | built |
| The decide node (the decision model picks a branch) | planned |
| The action registry with risk from each action's effect, and one action, `log_line` (`app/domain/actions.py`) | built |
| Telling a person: the `notify` action and `channels.tell` (their own chat, a notification target, a Matterbridge room) | built |
| The model and the agent: the `ask_model`, `call_tool` and `ask_agent` actions, and prompts with outside text marked | built |
| Calling a web address: the `http_request` action, with credentials in headers kept sealed | built |
| Running Lens work: the `run_workflow`, `run_pipeline` and `run_routine` actions | built |
| Changing assets and writing notes: the `set_field`, `add_to_collection` and `write_note` actions | built |
| Settings → Automations: how often followers look, how long the event log is kept, and how late a timer may fire | built |
| A webhook address per automation trigger, and calls to it as `webhook.received` ([Webhook trigger](#webhook-trigger)) | built |
| Timers that outlast a restart and fire once across the API and the worker (`app/domain/timers.py`, [Time](#time)) | built |
| The timers behind schedule, `at` and `relative` triggers, the `clock.tick` kind and what's coming ([Time](#time)); automations set them once they are stored | built |
| Actions that wait (`delay`, `when`) ([Time](#time)) | planned |
| The Timeline page with filters, grouping, review keys and new items, and Home's Latest card ([Timeline](timeline.md)) | built |
| Storing and running automations, joins, approvals, variables, the studio | planned |

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
| `comment.added` | | `comments` | a `Note`: `content`, `inReplyTo` (the comment it answers), `quote` (the passage), `t0`, `t1` (the moment, ms); `target` is the asset | Sam commented on “Call with the school” |
| `note.created` | | `note_pages` | a `Page`: `summary`, `about` (what it's the page of), `place`, `author` (person or assistant) | Assistant wrote the note “St Mary's School” |
| `note.saved` | | `note_versions` | as `note.created`, with `version` (the page as it was before) and `why` | Sam edited the note “School trip plans” |
| `clock.tick` | | `clock` (never stored) | an `Event`: `at` (the moment, UTC), `cron`, `timezone`, `local` (the time there, in words), `description` (the schedule in words, or once), `late`; actor `Lens clock` | Lens clock: Fri 16 Oct at 07:00 (every weekday at 07:00, Europe/London) |
| `entity.changed` | | `graph` | `op` (created, renamed, merged, split, linked, unlinked, deleted, rolled back, changed), `change` (the graph's own op), `entityType`, `before` (a renamed entity's name), `version`, `count`; tagged with its entities | Sam renamed “St Marys” to “St Mary's School” |
| `sensor.reading` | | `sensor_readings` | `{type: Note, id: lens:sensor_reading/<id>, name: "<stream>: <what it said>", url: /sensors/<id>}`, `stream`, `value`, `text`, `fields` (a JSON reading's numbers), `level` (syslog), `pattern`, `label` (the line's pattern and its label); noisy | Kitchen sensor temperature: 31.2 |
| `sensor.alert` | | `log` | `{type: Note, id: lens:sensor_pattern/<id>}`, `label` (alert or notable), `pattern`, `example` (a line it fits), `count` (lines in all), `lines` (in the batch that raised it), `hour` | 192.168.1.1 logged an alert: wlan<n>: authentication failed for <mac> |
| `sensor.joined` | | `sensors` | `{type: Service, id: lens:sensor/<id>, url: /sensors/<id>}`, `sensor_type` (mqtt, syslog, ...), `device`, `status` | A new MQTT device reported: zigbee2mqtt/kitchen |
| `chat.message` | | `chat_messages` | `{type: Note, id: lens:chat_message/<id>, name: the start of it, url: /chat/<id>}`, `text`, `channel` (web, telegram, slack, bridge, mailbox), `room`, `chat`, `direct` (a private chat or the web app), `attachments` (how many files) | sam wrote in Telegram: “groceries: milk, eggs” |
| `routine.ran` | | `routine_runs` | `{type: Note, id: lens:routine_run/<id>, url: /routines/<id>}`, `status` (done, partly, error, skipped), `trigger` (schedule, manual), `startTime`, `endTime`, `actions`, `failed`, `error`, `changes` (graph changes applied and proposed), `cost_usd`, `tokens` | Nightly tidy ran |
| `budget.warned` | | `log` | `{type: Note, id: lens:budget/<resource>, url: /costs}`, `resource` (all, space:<id>, routine:<id>, ...), `state` (near, over), `share`, `usd`, `tokens`, `estimate`, `cap_usd`, `cap_tokens`, `period`, `since` | The pods namespace has used 82% of its budget |
| `backup.finished` | | `log` | `{type: Document, id: lens:backup/<name>, url: /settings/backups}`, `label` (manual, scheduled), `ok`, `error`, `kind` (export, folder), `bytes`, `files`, `copied` (to the storage connection) | A scheduled backup finished: scheduled-20261015-020000.surql.gz |
| `lens.release` | | `log` | `{type: Note, id: lens:release/<tag>, url: its release notes}`, `tag`, `title`, `current` (what this server runs), `published_at` | Lens v0.7.0 is out (this server runs 0.6.0) |
| `webhook.received` | | `log` | `{type: Note, hook: the webhook's id, payload: what was sent (JSON, a form's fields or text), content_type, query}`; the actor is the webhook (`Service`, `lens:automation_hook/<id>`, "Webhook for <automation>") | Webhook for Front door was called |

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

A comment on an asset, or a reply on its thread, is `comment.added`, by whoever wrote it, with the asset as its
`target`. A note page is `note.created` when it is made (the assistant's own notes too) and `note.saved` when it
changes: at most one activity per page in each ten minutes of the clock (`note-<page>-<window>`), read from the
versions the notebook keeps of each change. A follower hears of a window's first change, as soon as it is made; the
timeline shows its newest. Every change to the entity graph (`graph_event`, [graph history](graph-history.md)) is one
`entity.changed`, about its first entity that is still there, tagged with up to twenty of its entities and in the
first of its namespaces. Its `op` says what happened in a word; its actor is the automation, the assistant or the
agent over MCP it was made through, else the person, the routine, or Lens (analysis). A change that spans namespaces
(a link between two) names entities of each, so only admins see it, with every namespace in `lens.spaces`.

Fields come from what the row keeps, so reading them costs nothing on the ingest path. A message's addressing is
kept as it is listed (`recording.mail`: who sent it to whom, its subject, list, mailbox, and Gmail's snippet), an
event's times, place and people as it is imported (`recording.event`), and an email's attachments and the start of
its text once it has been read (`recording.email`). Emails and events imported before these were kept have only the
fields their rows had. Outside text (titles, subjects, addresses, snippets, places, file names, page addresses) is
listed in `lens.untrusted`.

#### Sensors as activities

Built (`app/domain/events_streams.py`). A sensor's actor is the sensor (`Service`, `lens:sensor/<id>`, its name); a
sensor with no namespace makes activities for admins, as its page is. Its names, streams, text and patterns are
outside text.

- `sensor.reading` is a reading the sensor keeps, as its handling stores them: `all`, `changes` (a change, and once
  an hour while a value stays the same), none for `summary` or `none`, and none while it is paused or ignored. To
  trigger on every change, set the sensor to store changes. It is noisy: the timeline leaves it out unless asked for.
  Readings are read one sensor at a time over the index on `(sensor, at)`, and a follower whose hint names sensors
  (`{"actors": ["lens:sensor/5"]}`, which an automation's trigger on one sensor gives) reads only theirs, so a busy
  sensor nobody listens to is never read.
- `sensor.alert` is a log pattern ([Sensors](sensors.md#log-patterns-and-triage)) labelled alert or notable that new
  lines fit, raised by the intake as it writes them: once per pattern per hour (the event log's key is the pattern
  and the hour, so two processes raise it once), whether the lines are kept or dropped. A label the decision model
  wasn't sure of raises none until a person settles it, and a paused or ignored sensor raises none.
- `sensor.joined` is a device or host that made itself a sensor by reporting for the first time (MQTT, syslog),
  read from its `storage_source` row by `created_at`; a sensor added by hand isn't news.

#### Chat as activities

Built (`app/domain/events_streams.py`). `chat.message` is what a person said to Lens: a question in the web app (Chat,
the setup conversation) or a message to Lens in a chat app (Telegram, Slack, Matterbridge, the assistant's mailbox).
It reads the messages Lens already keeps (`chat_message` with role user), so nothing new is stored, and what isn't
said to Lens in a room is never kept, so it is never an activity. Its text and names are outside text.

- In the web app the actor is the person (`lens:account/<id>`), and the activity is theirs alone (`lens.audience`
  owner): not even admins or others in the namespace see it. Its namespace is the conversation's scope.
- In a chat app the actor is who wrote (`Person`, `lens:chat_user/<channel>:<their id there>`, their name there),
  since a chat user isn't a Lens account. A room given to a namespace's assistant belongs to that namespace; any
  other conversation (a private chat, an open room) is the answering account's alone. The conversation keeps where
  it is (`chat.via`: the channel, the room, who, a private chat or not) from its next message on.
- A question an automation's `ask_agent` asks (`origin` on the message) isn't a person's, so it is never a
  `chat.message`, and an automation can't set itself off that way.

#### Lens itself as activities

Built (`app/domain/events_streams.py`). What Lens does on its own is for admins, unless it is about one namespace.
Each is logged once, however many processes see it happen.

- `routine.ran` is a routine run that ended: done, partly (some actions failed), error, or skipped over budget once
  someone picked to skip it. Its actor is the routine (`Application`, `lens:routine/<id>`). A run held over budget is
  one when it ends; one someone let run anyway runs again as a new run, and that run is the activity. A routine of
  one namespace makes activities in it.
- `budget.warned` is a budget crossing its warning share (`warn_at`) or its cap, once per period and state, logged by
  the same check that alerts on it ([Budgets](budgets.md#warnings)). A namespace's budget makes it in that namespace.
- `backup.finished` is a backup taken in Settings or through the API, or on the schedule, which says whether it
  worked (a scheduled one that failed says why) and whether it was copied to the storage connection.
- `lens.release` is a newer Lens release, once per release, when Lens tells the admins and notification targets
  ([Updates](updates.md#being-told)).

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

A kind belongs to the source named in its `source`, and a source may list more in `kinds`. These are built:

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
- `comments` reads `comment` by `created_at`; `note_pages` reads `note_page` by `created_at`; `note_versions` reads
  `note_version` by `at` (to the millisecond) and keeps one version for each page and window; `graph` reads
  `graph_event` by `at`, the event number breaking ties (numbers are given out in order, so the two agree).
- `sensor_readings` (`app/domain/events_streams.py`) reads `sensor_reading` by `at` (microseconds), the id breaking
  ties, one sensor at a time, as `sensor.reading`; `sensors` reads the `storage_source` rows discovery made by
  `created_at`, as `sensor.joined`.
- `routine_runs` (`app/domain/events_streams.py`) reads `routine_run` rows that ended by `finished_at`, the id
  breaking ties, as `routine.ran`.
- `chat_messages` (`app/domain/events_streams.py`) reads `chat_message` rows with role user by `created_at` over the
  index on (role, created_at), the id breaking ties, as `chat.message`.
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
(`events.viewer(account, spaces, admin)`, spaces null for every one): `{"items": [...], "next": ...}`, where `next` (an
opaque cursor, null at the end) goes in `before` for the page after. It merges every source's `page` by `published`
(each source's own position breaking ties), applies who may see what, and when filters thin a page out it reads on, up
to five pages from each source. `events.get(db, cfg, id, viewer)` reads one activity, null when there is none or it
isn't the viewer's to see.

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

### Webhook trigger

Built (`app/domain/automation_hooks.py`, `POST /api/v1/automations/hooks/{token}`). A trigger `{"on": "webhook"}`
is a secret address anything that can send HTTP calls to set the automation off: Home Assistant's `rest_command`, a
phone shortcut, another service. No webhook exists until an automation with such a trigger is saved.

- `automation_hooks.token_for(db, cfg, automation_id, alias, signed=False)` makes the hook for one trigger when the
  automation is saved: `{"hook": id, "url": address, "path": address without the server, "secret": signing secret}`.
  The address and secret are shown this once; the row (`automation_hook {automation, alias, token_hash, secret,
  signed, created_at, last_called_at, calls}`) keeps only the token's hash and the secret sealed with the server's
  key. Calling it again for the same trigger replaces the hook, and the old address stops working. The trigger keeps
  the hook's id in `hook`. `automation_hooks.revoke(db, hook)` deletes it (when the trigger or the automation goes);
  `for_automation(db, id)` lists an automation's hooks.
- A call needs only the token in the address (`lah_...`). It takes JSON, a form (text fields; files are refused) or
  text, up to 64 KB, at most 60 calls a minute per hook (in the API process; 429 with `Retry-After` past it), and
  answers 202 `{"id": activity}` at once. 401 for a token that isn't a hook's, 403 while the automation is switched
  off, 413 over 64 KB, 415 for anything but JSON, a form or text.
- What was sent becomes a `webhook.received` activity in the event log, with the payload, its content type and the
  query as outside text. Its namespace is the automation's (admins see it while the automation isn't stored).
- Signed calls: a caller that signs the Standard Webhooks way (`webhook-id`, `webhook-timestamp` in seconds,
  `webhook-signature: v1,<base64 HMAC-SHA256 of "<id>.<timestamp>.<body>">` keyed with the secret, as Lens signs its own
  [notification webhooks](notifications.md#webhooks)) has the signature checked, and the time must be within five
  minutes. A hook made with `signed=True` refuses calls that aren't signed. A call naming its message (`webhook-id`, or
  an `Idempotency-Key` header) is logged once however often the sender retries it.
- In Docker the API runs no background work, so the worker's automations see the call on their next look (5
  seconds, `events.poll_seconds`).

Lens has three kinds of webhook address; they don't share tokens or do each other's jobs:

| Address | Made by | What a call does |
| --- | --- | --- |
| `POST /api/v1/automations/hooks/{token}` | saving an automation with a webhook trigger | sets that automation off with what was sent (`webhook.received`); nothing is kept but the activity |
| `POST /api/v1/sensors/push/{token}` ([Sensors](sensors.md)) | an admin adding a webhook sensor | keeps readings (numbers, on/off, text) for charts, with the sensor's handling |
| `POST /api/v1/hooks/import/{token}` ([Import webhooks](api.md#import-webhooks)) | a namespace's owner | imports files, web pages or text as assets |

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

### The event workflow scope

Built: what an automation does is a workflow of scope `event` (`app/domain/automation_nodes.py`), saved and
versioned like any workflow (`POST /api/v1/workflows` with `"scope": "event"`). Its `input` node passes on the
[run context](#run-context), and between the primitives (condition, switch, set, merge, loops...) it has two nodes of
its own:

- **Action** (`action`, configured `{action, params, delay?, when?}`): one action from the registry. When it runs,
  it fills its params in from the run context (`"text": "New: {{ event.object.name }}"`; a param that is exactly one
  `{{ }}` expression keeps its type, so a list stays a list; the action's defaults are filled in first, so a default
  that is a template, like `ask_agent`'s `about`, is filled in too; a prompt param is rendered with outside text
  marked, as [The model and the agent](#the-model-and-the-agent) says), works out its effect and risk and makes an
  [intent](#actions-and-intents). A routine intent is carried out there and then: what it gives back passes on along
  `out` and goes in `steps.<node id>` for later nodes. One that asks first waits for the person's pick, and one with
  a `delay` (like `30m`) or a time (`when`, a time or a template giving one) waits for its time: such a node passes
  nothing on, so the nodes after it are skipped and the run's log says where it stopped and why. An action that
  fails is marked failed on its node, and the rest of the graph goes on.
- **Decide** (`decide`, configured `{question, options, input?, min_confidence?}`): the decision model picks one of
  its options, with an output port per option and `unsure`. Planned with the decision model; until then a decide node
  is refused when the workflow is saved, as coming soon. Use a condition or switch node.

Checked when it is saved, on every path (create, a new version, a body):

- It needs an action node, each action is one the registry has (`GET /api/v1/automations/actions` will list them)
  with params that fit its schema, and each template in its params is valid.
- **The leaf rule:** an action that reaches outside Lens or other people (effect `out`, worked out from its params)
  or that waits has nothing connected after it, as it can't pass anything on while it waits for a yes or its time.
- Waiting before an action (`delay`, `when`) is refused as coming soon until timers are built.

Its automation runs it: an event workflow isn't run on an asset, as a pipeline step or as a routine's action, and the
canvas's list of workflows leaves it out (`GET /api/v1/workflows?scope=event` lists them).

**Try it** with `POST /api/v1/workflows/test` (admins): `{"scope": "event", "graph": ..., "kind": "asset.added"}`
runs it on that kind's sample, and `"activity": "<id>"` on a real activity you can see. Nothing is kept and nothing is
sent: each action says what it would do (`would write “New: Call with the school” to the run log`), and `intents`
lists each one with its params, effect, risk, status (`done`, `waiting`, `scheduled` or `failed`) and what it gave
back. Text from outside Lens (`lens.untrusted` on the activity, or what a node passed on when its output says
`untrusted`, like a web page's answer) that reaches an action's params marks its intent `untrusted`.

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
| `run_workflow` | runs a recording `workflow` (its published `version`, or the one named) on an `asset` as a job | lens | `{job, workflow, version, asset}` |
| `run_pipeline` | processes an `asset` again with a `pipeline` (or the one its namespace uses), keeping its outputs | lens | `{job, pipeline, asset}` |
| `run_routine` | asks the scheduler to run a `routine` now (`propose_only` to only propose its changes) | lens, so it asks | `{routine, requested, propose_only}` |
| `set_field` | sets one of an `asset`'s custom fields (`field`: its id or label) to `value` (empty clears it) | lens | `{asset, field, label, value, changed}` |
| `add_to_collection` | moves an `asset` into a `collection` of its namespace | lens | `{asset, collection, name, moved, newly_see}` |
| `write_note` | writes `text` (Markdown) `on` a notebook `page` (with a `title`, `about` a thing, in a `namespace`) or on an `asset` (`shared` or not) | self for a private note on an asset, else lens | `{on, page or note, added, url}` |

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

#### Running Lens work

`run_workflow` and `run_pipeline` take an `asset`: an asset id, or the activity's object (`lens:recording/<id>`, which
is the default, `{{ event.object.id }}`). On the asset the activity is about, in the automation's namespaces, they run
without asking; on any other, they ask first. They need the automation's person to be able to process assets in that
namespace, queue a job (after whatever the asset's job still has to do) that shows as by "automation: <name>", and pass
on the job's id. `run_routine` asks the scheduler to start a routine's run within half a minute, as Run in Settings
does: routines are an admin's, the routine must work only in the automation's namespaces, a run over one of its budgets
is refused, and it always asks first. The run's id shows in the routine's runs.

#### Changing assets and writing notes

`set_field` and `add_to_collection` take an `asset` like the Run actions and need the automation's person to be able
to edit assets in that namespace. A field's change goes in the asset's metadata history as by "automation: <name>", so
it can be reverted like any edit, and both are in the audit log. Moving an asset into a collection also shows it to the
people given a role on that collection: when that would let someone see it who can't now, the action never does it
on its own (it fails, naming them) and moves it only once you approved it.

`write_note` writes a notebook page in the namespace of what it is `about` (an asset, entity, topic, collection or
speaker, which has one page each: a second note about it goes at that page's end, its history keeping the rest), or
the one named, or the automation's only namespace; the page is written by the automation. `on: asset` writes a note on
the asset instead, as yours: a private one (the default) only you see, so it runs at once, while a `shared` one
everyone who reads the asset sees.

Calendar events aren't written: Lens signs in to Google Calendar read-only, and writing needs a new consent. Meanwhile
the agent can draft an event as a note.

## Time

Time is a trigger and a step: "every weekday at 7:00", "at 9:00 on the 20th", "30 minutes before a calendar event",
"wait 2 hours, then...", and the clock behind a join's "if no reply within 2 days". All of it rests on timers.

### Timers

Built (`app/domain/timers.py`). A timer is one row per key in the `timer` table: when it is due, what runs then (its
kind), what that gets (its payload), and the automation it is for. Status: `waiting`, then `fired`, or `failed`,
`cancelled` or `missed`.

```python
timers.set(db, key, at, kind, payload, automation=None)  # makes it, or replaces the one with that key
timers.cancel(db, key)                                    # True when one was waiting
timers.cancel_for(db, automation)                         # every waiting one of an automation (deleted, switched off)
timers.get(db, key)

@timers.handler("join.absent")                            # what runs when one of this kind fires
def _(db, cfg, payload): ...                              # payload + late (true or false) + timer {key, at, automation}
```

- **Once.** A thread started with the routines' threads (so in the API's background work and in `lens worker`) looks
  every 5 seconds, and at once after `events.wake()`. Each due timer is claimed with a compare-and-set update that
  only one process wins, then its handler runs. A process that stops while a handler runs leaves that firing done:
  a timer fires at most once, never twice.
- **Repeating.** A handler registered with `again(payload, after)` (a schedule's next time) is re-armed in the same
  update that claims it, so a repeating timer is never lost; its row keeps waiting, with `last` saying how the last
  firing went.
- **Lens was off.** A timer due within `automations.catch_up` (1 hour; Settings → Automations) fires late, with
  `late: true`; an older one is `missed` and counted on its automation (`missed`). A repeating one that missed
  several times fires once, for its latest time if that is within the window, then waits for its next. A timer fired
  within 2 minutes of its time is on time.
- **Kept small.** An automation has at most 1000 timers waiting; setting more is refused, saying so. Timers that
  ended are dropped after 7 days, by a tidy `routines.run_due` calls (once an hour). A kind nothing handles fails
  with a note.
- **Light.** No timer exists until an automation uses time. While none waits the thread doesn't query the table: it
  checks once a minute, and at once when this process sets one. A timer set by the other process (the API, when the
  worker fires them) can so fire up to a minute late.

### Schedule, at and relative triggers

Built as timers; automations set them once they are stored. Each trigger checks as
[Automations: triggers and joins](#automations-triggers-and-joins) says.

```python
timers.schedule_automation(db, automation)   # {id, enabled, spaces, triggers}: on save, switch on, switch off
timers.relative(db, automation, alias, activity, field, offset)   # a matching activity came: {key, at, set, note}
timers.upcoming(db, automation, n=10)        # what it has waiting, soonest first, in words
timers.clock_state(db, automation)           # each schedule and at trigger: waiting, done, missed, failed or off
```

- **Schedule** (`on: schedule`, `cron`, `timezone`): one timer, `sched-<automation>-<as>`, at the schedule's next
  time (`schedule.py`: five-field cron, the shorthands, time zones; a time daylight saving skips runs when the clock
  gets past the gap, a time it repeats runs once). When it fires, the automation fires with a `clock.tick` activity
  for that moment, and the timer is re-armed for the next time in the same update.
- **At** (`on: at`, `at`, `timezone`): one timer, `at-<automation>-<as>`, if the time is still to come; afterwards
  the trigger reads done, and saving the automation again doesn't set it again.
- **Relative** (`on: relative`, `kinds`, `where`, `field`, `offset`): when a matching activity comes, a timer at the
  time in its `field` plus `offset` (`-30m`: half an hour before; a date alone is its midnight in UTC), keyed by the
  automation, the trigger and the activity's object (`rel-<automation>-<as>-<object id>`). So when a calendar event
  moves (`calendar_event.changed`, about the same object) its timer moves with it rather than adding a second. A time
  more than 5 minutes in the past is skipped, with a note for the automation's log, and a timer it had is called
  off. When it fires, the automation fires with the activity that set it.
- **Saving again** leaves a timer a trigger still wants as it is, so one that came due while Lens was off can still
  catch up. A changed schedule or time sets it anew, a trigger that's gone has its timer called off, and waiting
  relative timers follow a changed `field` or `offset`. Switching an automation off calls off every timer it has.
- **What's coming** (`upcoming`): `{key, at, kind, trigger, node, description, untrusted}`, such as "Every weekday at
  07:00: next on Fri 16 Oct at 07:00 (Europe/London)" or "30 minutes before “Parents' evening” starts: on Tue 20 Oct
  at 16:30 (UTC)"; `untrusted` says the description quotes outside text (the event's title). The builder previews a
  schedule with `GET /api/v1/routines/schedule`, which now reads weekdays, single days and days of the month in
  words too ("every weekday at 07:00", "at 09:00 on the 20th of every month").

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
- `note.saved` comes as a window's first change is made, while the person may still be typing; an automation can
  wait a little before it reads the page (M6). A page made and then written on shows both `note.created` and
  `note.saved`.
- A graph change across namespaces is for admins; show it to whoever reads all of its namespaces, as the graph's
  history does.
- Every processed asset's analysis is an `entity.changed` (op created or changed); the timeline page could fold them
  into the asset's `asset.processed`.
- SurrealDB 3.2.4 gives a range's first rows, not its last, for `ORDER BY ... DESC LIMIT n` over an index, so
  sources write a page's time bounds so that no index range is used, and its cursor (`events.after_sql`) reads the
  table in full. Paging is right; give it one ordered index read per page once SurrealDB does that right.
- Telling a person: buttons on chat approvals (Telegram inline keyboards, Slack Block Kit); email as a `notify`
  channel; web push; private chats through Matterbridge (it can't tell them from rooms); with several people let in,
  "me" is whoever last wrote privately, until each chat user has a Lens account of their own; telling which links in a
  message the person's template wrote, so they stay when the run read outside text.
- The canvas draws and edits event workflows (today they are saved and tried through the API).
- An action's outputs in the variable picker, typed from its `output` schema.
- The model and the agent: per-node model picks in the studio; streaming an agent's steps into the run's log as they
  happen; a namespace assistant's memory for automations (`remember` is left out today); approving an agent's
  proposals from the chat app it told you in; count the automation's own budget once budgets take `automation:<id>`.
- Calling a web address: secrets in the address itself (a Slack or Discord webhook URL) sealed like headers; PATCH and
  DELETE (DELETE would count as deleting); OAuth sign-in for an API; form-encoded bodies; retries with backoff;
  networks allowed for automations apart from notifications.
- Time: calendar-aware schedules ("first workday of the month", holidays); sunrise and sunset triggers; an all-day
  event's date in the person's time zone rather than UTC; upcoming relative timers in the person's time zone; an
  event that's cancelled calling off its relative timers; the timeline's kind filter leaving out `clock.tick`, which
  is never stored; sub-second timers woken by `events.wake()` alone; telling the other process (the worker) at once when the
  API sets a timer, rather than within a minute.
- Running Lens work: a routine run's id passed on (today the scheduler starts it later); waiting for the job and passing
  on its outputs; roles on collections counting for `process`; running a pipeline on every asset a join collected.
- Changing assets: asking first instead of failing when a move would show an asset to new people (risk that reads the
  database); fields on collections and files; tags; roles on collections counting for `edit`.
- Writing calendar events (a new Google consent, so it changes existing installs).
- Webhook trigger: signed answers to the caller (what the run did); a rate limit shared by every API process (today
  each counts its own); showing a hook's last call in the studio.
- Sensors: a threshold per reading in the intake (only readings over a value become activities, for sensors that
  keep everything); a reading's unit, which sensors don't record yet; an alert as soon as a pattern is labelled, for
  the lines already seen that hour.
- Chat: recording every message in a room as an activity (opt-in per channel, off by default); a chat user who is
  also a Lens account as that person; a timeline page of others' private messages is read and left out, up to five
  source pages, so narrow it by account if that proves slow.
- Lens itself: a run held over budget, waiting for someone's pick, as an activity of its own; backups taken before
  upgrades or with `lens backup` (only those from Settings, the API and the schedule are activities today).
