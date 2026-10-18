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
| Looking back over the stream in an automation's If (`seen`) | built |
| Joins: `all`, `count`, `absent` and `batch`, completed as the stream brings each trigger's activities and as their timers fire ([Joins](#joins)) | built |
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
| Reactions and comments on activities, as `reaction.added` and `comment.added` ([Timeline](timeline.md#reactions-and-comments)) | built |
| The shapes of triggers, joins, an If, policy and intents, checked (`app/domain/automation_spec.py`) | built |
| The event workflow scope: what an automation does, drawn with action nodes and tried on an activity (`app/domain/automation_nodes.py`) | built |
| The decide node: the decision model picks a branch, one port per option and `unsure` ([The decide node](#the-decide-node)) | built |
| Drawing an automation's workflow on the canvas: the actions in the palette, their settings from each action's params, Decide's ports, the leaf rule and a try on a kind's sample ([On the canvas](#on-the-canvas)) | built |
| The action registry with risk from each action's effect, and one action, `log_line` (`app/domain/actions.py`) | built |
| Telling a person: the `notify` action and `channels.tell` (their own chat, a notification target, a Matterbridge room) | built |
| The model and the agent: the `ask_model`, `call_tool` and `ask_agent` actions, and prompts with outside text marked | built |
| Templates in params and prompts: the run context, one expression keeping its type, errors that name the nearest variable, saved prompt templates, filters for times, text and addresses ([Variables and templates](#variables-and-templates)) | built |
| Saved variables: `vars` in the run context, the `set_variable` action, the API and MCP tools ([Saved variables](#saved-variables)) | built |
| The variable picker's catalog and a preview of what a template gives, through the API and MCP ([The variable picker and preview](#the-variable-picker-and-preview)) | built |
| Calling a web address: the `http_request` action, with credentials in headers kept sealed | built |
| Running Lens work: the `run_workflow`, `run_pipeline` and `run_routine` actions | built |
| Changing assets and writing notes: the `set_field`, `add_to_collection` and `write_note` actions | built |
| Settings → Automations: whether automations run, their limits, how long runs are kept and how late a timer may fire; how often followers look and how long the event log is kept | built |
| A webhook address per automation trigger, and calls to it as `webhook.received` ([Webhook trigger](#webhook-trigger)) | built |
| Timers that outlast a restart and fire once across the API and the worker (`app/domain/timers.py`, [Time](#time)) | built |
| The timers behind schedule, `at` and `relative` triggers, the `clock.tick` kind and what's coming ([Time](#time)), set when an automation is saved | built |
| Actions that wait (`delay`, `when`): kept as timers, carried out at their time, asking first when they should then ([Time](#time)) | built |
| The Timeline page with filters, grouping, review keys and new items, and Home's Latest card ([Timeline](timeline.md)) | built |
| Approvals: an automation's action that asks first waits for your pick (approve, decline, edit, allow from now on), told once in your chat app, capped and expiring (`app/domain/approvals.py`) | built |
| Deciding an automation's action with a yes in the chat it told you in | built |
| Approvals as activities (`approval.waiting`, `approval.decided`) and the `approval.waiting` notification | built |
| Agents' approval tools: `approvals` and `decline_approval` over MCP and in the assistant, and no way for an agent to approve | built |
| The Approvals page (`/approvals`), its badge in the top bar and nav, and Home's Needs attention row | built |
| Saving automations: checks, namespaces, switching on and off, delete and restore, the policy, the API (`app/domain/automations.py`) | built |
| Running automations: following the stream, limits, budgets, runs as `automation.ran`, trying and running now, the API (`app/domain/automations.py`) | built |
| Automations for agents: the MCP tools and the assistant's, switching on and running now by approval | built |
| Describing an automation in words: a draft from a sentence (`POST /api/v1/automations/draft`) | built |
| The studio: the list at `/automations` and the When, If and Then builder, with a test panel ([The studio](#the-studio)) | built |
| An automation's page: its runs, what's coming up, its variables and its costs, in tabs ([An automation's page](#an-automations-page)) | built |
| Starters: five ready-made drafts, switched off, to open, change and save (`GET /api/v1/automations/starters`, [Starters](#starters)) | built |

## The studio

Automations (under Automate in the nav) lists every automation you can see. [Schedules](schedules.md), everything
that runs on a clock, is its other tab, so both share one row in the nav; for admins, Routines and New routine are at
the top of both. Each row reads its triggers in words
(“Email received”, where the subject contains “trip”), and shows how its last run went, its runs today, the actions
waiting for your pick (a link to Approvals), what it cost this month and when it runs next. The switch turns it on
or off. Its menu has Run now, Duplicate (a copy, switched off) and Delete, which you undo from its toast rather than
confirm first.

New automation opens the builder, with the cursor in **Describe it in words**: type a sentence ("when an email about
a school trip arrives, tell me") and press Enter, and the model fills the builder in, saying what it left out. Or
pick one of the [starters](#starters) under it, which fills the builder in and says what to change first. Or build it
yourself:

- **When**: one card per trigger. An activity kind has **Only when** rows (a field, a test that suits its type and
  a value); a schedule has presets or a cron line, its time zone and its next few times; a time is a date and time;
  a webhook's address shows once you save. With two or more triggers, the join line says whether any one of them
  starts it or it waits for all of them.
- **If**: rows tested on what happened, all or any of them, with the fields of the variable catalog to pick from.
- **Then**: one card per action, in order. Each card's settings come from the action's params; typing `{{` in a
  text field opens the variable picker (arrow keys, Enter or Tab to put one in, Esc to close). A badge says whether
  the action runs on its own or asks you first, **Wait first** delays it, and once the automation is saved **Let it
  run on its own** stops an outside action asking (`POST /api/v1/automations/{id}/policy`).

Enter in a row's last field adds a row. **Test** runs the draft on a recent activity, or on a kind's sample, and
lists what each action would do; nothing is changed. **Save and turn on** saves it switched on, **Save** keeps it as
it is (a new one stays off), and Ctrl or Cmd+Enter saves from anywhere. The API's problems show next to the trigger,
row or action they are about.

An automation whose workflow branches (a Decide node, or an action after a condition) can't be read as a list:
the builder says so and opens it in the [canvas](#on-the-canvas), where When and If still edit.

### An automation's page

An automation's page has its switch, Run now and five tabs (`?tab=` in the address opens one):

- **Edit**: the builder.
- **Runs**: newest first, 20 at a time with Older runs for the next page. Each reads how it went, what started it,
  what its actions did and what it cost; opening one shows each action's outcome and the run's log. A run held over a
  budget offers Run it once or Skip. Run now opens this tab.
- **Coming up**: its next schedule times, `at` times and actions that wait, soonest first.
- **Variables**: what it keeps between runs ([Saved variables](#saved-variables)). Edit a value as text or JSON and
  press Enter; Clear is undone from its toast; a new name and value are added with Enter.
- **Costs**: its budget and its activity: the calls it made and what they cost.

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
| `feed_post.received` | `asset.added` | `recordings` | `href` (the post's address), `site`, `feed` (its title), `author`, `published`, `categories` | Harbour news received the post “Autumn regatta” from harbour.example.org |
| `file.added` | `asset.added` | `recordings` | `path` (in its source), `mediaType`, `size` (bytes), `duration` (seconds) | Office share added “Minutes” |
| `upload.added` | `asset.added` | `recordings` | `path` (the file's name), `mediaType`, `size`, `duration` | Sam uploaded “Call with the school” |
| `web_page.saved` | `asset.added` | `recordings` | `href` (the page's address), `site` | Sam saved “Museum opening hours” from museum.example.org |
| `import.received` | `asset.added` | `recordings` | `via`: hook (an import hook), paste (pasted text) or file (a file read as text) | Phone shortcut imported “Shopping list” |
| `asset.processed` | | `jobs` | the asset's fields, `job` (`lens:job/<id>`), `steps`, `batch`; tagged with its entities | “School trip on Friday” finished processing (pages, analyze, embed) |
| `asset.failed` | | `jobs` | as `asset.processed`, with `step` (where it failed) and `error` | Processing “Call with the school” failed at transcribe |
| `asset.deleted` | | `log` | `type`, `id`, `name`, `media` | Sam deleted “Old minutes” |
| `calendar_event.changed` | | `log` | `startTime`, `endTime`, `location`, `calendar`, and `before` (`startTime`, `endTime`) | Family calendar moved “Parents' evening” to 2026-10-21 18:00 |
| `comment.added` | | `comments` | a `Note`: `content`, `inReplyTo` (the comment it answers), `quote` (the passage), `t0`, `t1` (the moment, ms); `target` is the asset, or the activity of the timeline it was written on (`lens:activity/<id>`) | Sam commented on “Call with the school” |
| `reaction.added` | | `log` | the activity reacted to (an `Activity`, `lens:activity/<id>`, `name` its summary), `emoji`, `activity` (its id); noisy | Sam reacted 👍 to “Work Gmail received “Parents' evening”” |
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
| `approval.waiting` | | `approvals` | `{type: Question, id: lens:approval/<id>, name: what it would do, url: /approvals?id=<id>}`, `status`, `action`, `effect`, `automation`, `automation_name`, `run`, `node`, `about` (the activity that caused it), `expires_at`; its person alone sees it | School emails asks for your pick: Post in the Matterbridge gateway family: “Trip on Friday, bring a packed lunch” |
| `approval.decided` | | `approval_decisions` | as `approval.waiting`, with `status` (done, failed, declined, expired) and `error`; `Accept` when approved, `Reject` when declined or expired | Sam approved: Post in the Matterbridge gateway family: “Trip on Friday, bring a packed lunch” |

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
`target`; a comment written on an activity of the timeline has that activity as its `target`
(`lens:activity/<id>`), and an emoji given to one is `reaction.added`, with the activity as its object
([Timeline](timeline.md#reactions-and-comments)). A note page is `note.created` when it is made (the assistant's own notes too) and `note.saved` when it
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

#### Approvals as activities

Built (`app/domain/events_approvals.py`). An automation's action that asks first ([Approvals](#approvals)) is
`approval.waiting` when it starts to wait, read from the approval by `created_at`, and `approval.decided` when it is
approved (done or failed), declined or expires, read by `decided_at`. Both are the person's own (`lens.audience`
owner): the timeline shows them to that person alone, not even to admins, and the person's automations can react to
them ("when an approval has waited a day, remind me"). The actor of `approval.waiting` is the automation (the
assistant for its own approvals); of `approval.decided`, the person who decided, or Lens when it expired. An
automation's approval carries its run as `lens.cause`, so a follower reacting to one can't loop for ever. What it would
do (`object.name`) and why it failed hold text the run read from outside Lens, and are listed in `lens.untrusted`.

### Sources and the event log

Built. A source (`events.Source`, added with `events.register_source`) reads rows Lens already keeps as activities,
so nothing is stored twice:

- `read(db, cfg, pos, spaces, limit, hint)`: activities strictly after `pos` (`[time, id]`, or `[time, null]` for
  after every row at that time), oldest first, at most `limit`, of the namespaces in `spaces` (null: all). `hint`
  is narrowing a source may use, like `{"actors": ["lens:sensor/5"]}`: what the triggers that want its kinds let
  through.
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
- `approvals` (`app/domain/events_approvals.py`) reads `approval` by `created_at`, the id breaking ties, as
  `approval.waiting`; `approval_decisions` reads the decided ones by `decided_at`, as `approval.decided`. Both page
  only the viewer's own approvals.
- `log` reads `event_log`: activities no row of their own records (a deletion, a webhook call), written by
  `events.emit(db, kind, *, actor, obj, space, summary, target=None, tag=None, key=None, published=None, spaces=None,
  audience=None, owner=None, untrusted=None)`, which gives the id (`log-<n>`). Emitting a `key` again changes
  nothing and gives the first id, so a retry never doubles an activity. Inside `with events.caused_by({"automation":
  4, "run": 51, "depth": 1}):` what is emitted carries that cause. It is read by `(logged_at, id)` and paged by
  `published`, and its rows are kept for `events.keep_days` (30; Settings → Automations).
- `automations` (`app/domain/events_automations.py`) reads `automation_run` by `finished_at`, the id breaking ties,
  as `automation.ran`: a run that ran its graph (done, partly, failed or waiting), with the automation as its actor
  (`Application`, `lens:automation/<id>`), the run and what its actions did as its object, and what started it as its
  `lens.cause` when that was another automation's run. An automation over a limit is logged as `automation.limited`,
  at most once an hour each.

### Following the stream

Built. The API and `lens worker` are separate processes on one database, so following works the way notifications
do: `events.follow(db, cfg, consumer, kinds=None, spaces=None, limit=500, hint=None)` gives `(activities, commit)`,
the activities since the consumer's last commit, oldest first. Handle them in order, then call `commit()` to save
where the next look starts (`event_cursor:<consumer>`, a position per source).

- A consumer seen for the first time starts at now: nothing that happened before replays, not even through the
  lookback. What happens in the second it starts in isn't seen.
- `hint` gives each source its own narrowing, by source name: `{"sensor_readings": {"actors": ["lens:sensor/5"]}}`
  (`match.compile_triggers` makes it, as [Running automations](#running-automations) says).
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

#### Looking back

Built (`match.seen`). An automation's If (and only its If, so testing a trigger's `where` on each new activity stays
cheap) can ask whether something happened before: a `seen` stands where a test would.

```json
{"seen": {"kinds": ["email.received"], "within": "2d", "same": "object.from",
          "where": {"path": "object.subject", "op": "matches", "value": "trip|outing"},
          "count": {"at_least": 1}}}
```

- It finds activities of `kinds` (and the kinds under them) published in the last `within` before now, passing its
  own `where`, and, with `same`, holding the same value at that path as the activity that started the run (the run
  context's `event`). That activity itself doesn't count.
- `count` is `{"at_least": n}` (1 when not given), `{"at_most": n}` or both, up to 100: `{"at_most": 0}` reads
  "if nothing like it came", as in "if I haven't replied within 2 days".
- It reads at most 200 activities, of the automation's namespaces (`env["spaces"]`) and as the automation's person
  (`env["viewer"]`, or `env["account"]`) may see them; `env["db"]` and `env["cfg"]` give the database. Without a database it finds
  nothing. Its `where` is tested with the same `env` (so its clock too) and can't test `changed`.
- In words: `at least one of “Email received” in the last 2 days, with the same sender`.

In a workflow, a condition, filter or switch case takes either one test, as before, or a tree in `match`; with a
tree, `path` (when given) is the part of the value the tree tests. Graphs saved before keep running unchanged.

```json
{"id": "school", "type": "condition",
 "config": {"path": "object", "match": {"all": [{"path": "from.0", "op": "contains", "value": "@stmarys.org"},
                                                {"not": {"path": "attachments", "op": "gt", "value": 5}}]}}}
```

### Automations: triggers and joins

Built as shapes with checks (`app/domain/automation_spec.py`), saved ([Saving automations](#saving-automations)) and
run ([Running automations](#running-automations)), joins included. Each check gives a cleaned copy, or a ValueError
naming the first problem.

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

#### Joins

Built (`match.join_step`). Running automations hands each activity a trigger matched (after its guards and its
claim) to `match.join_step(db, cfg, automation, alias, activity)`, which gives a list of completions, each
`{"as": alias, "events": {alias: activity or [activities]}, "event": the last}`, and each completion fires the
automation: its If and graph see every trigger's activity in `events` (`{{ events.mail.object.subject }}`), and
`event` is the last. Triggers a join doesn't name fire on their own, as with `any`. Windows are measured between the
activities' `published` times.

| Mode | Example | Completes |
| --- | --- | --- |
| `any` | `{"mode": "any"}` | at once, for each activity |
| `all` | `{"mode": "all", "within": "1d", "key": "object.from.0"}`: an email from the school and a calendar event from the same sender within a day | when every trigger has an activity within `within` of the others (on the same `key`); each trigger's latest one goes in `events`, then it starts again |
| `count` | `{"mode": "count", "of": "login", "at_least": 3, "within": "5m", "key": "actor.id"}`: three failed logins from one device in five minutes | when `of` has `at_least` activities within `within` (per `key`), with the list of them; then it counts again |
| `absent` | `{"mode": "absent", "after": "mail", "missing": "reply", "within": "2d", "key": "object.thread"}`: no reply within two days | when `within` passes after `after` with no `missing` on the same key published after it, with the first `after` activity; a `missing` one in time cancels it |
| `batch` | `{"mode": "batch", "of": "alert", "within": "1h"}`: the hour's alerts, acted on once | `within` after the first activity, with every one collected (up to 500), by trigger |

- What a join waits on is kept in `automation_state` (kind `join`) with small copies of the activities (long text
  cut), written so two processes at once lose nothing (a version on each row). A row expires `within` after its
  newest activity, or a day after an `absent` or `batch` timer's time.
- `absent` and `batch` set [timers](#timers) for their automation (`absent-<automation>-<key>`,
  `batch-<automation>`), so they outlast a restart and fire once; their handlers, `join.absent` and `join.batch`
  (`match.TIMER_HANDLERS`), fire the automation. A `missing` activity in time calls its timer off. A timer that ends
  without firing (the automation is switched off, or Lens was off past `automations.catch_up`) drops what it waited
  on, so the next activity starts afresh; one that can't be set (the automation has 1000 timers waiting) drops it
  too, with a line in the log.
- `match.describe_trigger(t)` reads a trigger in words (“Email received”, where the subject contains “trip”; every
  weekday at 07:00 (Europe/London); a call to its webhook), and `match.describe_triggers(triggers, join)` an
  automation's triggers, then its join when there are several.
- `match.describe_join(join)` reads a join in words: `all of the triggers within 10 minutes, for the same
  object.from`; `3 times login within 5 minutes`; `mail, then no reply within 2 days`.

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
| `now`, `now_local`, `timezone` | when the run started, in UTC and in the run's time zone, and that time zone |
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
  its options, with an output port per option and `unsure` ([The decide node](#the-decide-node)).

Checked when it is saved, on every path (create, a new version, a body):

- It needs an action node, each action is one the registry has (`GET /api/v1/automations/actions` will list them)
  with params that fit its schema, and each template in its params is valid.
- **The leaf rule:** an action that reaches outside Lens or other people (effect `out`, worked out from its params)
  or that waits has nothing connected after it, as it can't pass anything on while it waits for a yes or its time.
- Waiting before an action takes a `delay` (more than 0, like `30m`) or a `when` (a time, or a template giving one),
  not both; it is kept as a timer ([Actions that wait](#actions-that-wait)).

Its automation runs it: an event workflow isn't run on an asset, as a pipeline step or as a routine's action, and the
canvas's list of workflows leaves it out (`GET /api/v1/workflows?scope=event` lists them).

**Try it** with `POST /api/v1/workflows/test` (admins): `{"scope": "event", "graph": ..., "kind": "asset.added"}`
runs it on that kind's sample, and `"activity": "<id>"` on a real activity you can see. Nothing is kept and nothing is
sent: each action says what it would do (`would write “New: Call with the school” to the run log`), and `intents`
lists each one with its params, effect, risk, status (`done`, `waiting`, `scheduled` or `failed`) and what it gave
back. Text from outside Lens (`lens.untrusted` on the activity, or what a node passed on when its output says
`untrusted`, like a web page's answer) that reaches an action's params marks its intent `untrusted`.

#### On the canvas

Built: an event workflow opens on the workflow canvas (`/workflows/<id>`) like any other. It starts from **What
happened**, the run context. The palette lists every action in the catalog under **Actions**; dropping one adds an
action node set to it, and its settings are a form drawn from the action's params (a template field takes `{{ }}`),
with a badge saying whether it runs on its own or asks you first, and **Wait first** for a delay. **Decide** sits
under AI: write its question and its options one a line as `name: what it means`, and each option becomes an output
port, with `unsure` last. The canvas says what the backend would refuse before you publish: an action not chosen or
a required param left empty, a wait that isn't like `30m`, a decide node without a question or with fewer than two
options, and the leaf rule (nothing connected after an action that reaches outside Lens or waits). **Try on a
sample** runs the graph on the sample of the kind you pick: nothing is kept or sent, each node shows what it passed
on, and each action says what it would do (run at once, ask you first or wait for its time).

#### The decide node

Built (`match.check_decide`, `match.run_decide`). A decide node asks the decision model (Jev, then Laya, then the
language model as a fallback, as in [Routine choices](assistant.md#routine-choices)) a judgement call, and the run
goes on along the port of the option it chose:

```json
{"id": "n4", "type": "decide", "config": {"question": "Does this need me today?",
 "options": {"urgent": "Needs an answer today.", "later": "Can wait."}, "min_confidence": 0.8}}
```

- `options` names 2 to 10 options, in lowercase letters, digits and `_`, each with what it means. The node's ports
  are the options, then `unsure`; wire an edge from each port you want to act on.
- `input` is a template of what the model reads, rendered as [prompts](#prompts) are (each value from outside Lens
  marked as data), with `input` holding what reached the node: `Subject: {{ event.object.subject }}`. With none, it
  reads the event's summary and its outside text (the fields in `lens.untrusted`), marked as outside text
  (`untrusted.wrap`). Either way instructions in an email are read as content.
- The run goes along the chosen option when the model is sure enough: `min_confidence`, else
  `decisions.act_above` (default 0.8). Otherwise it goes along `unsure`, as it does when no decision model or
  language model is set up, or the budget's hard limit is reached. Along the port passes the decision, `{choice,
  confidence, ranked, by, sure}`, and `steps.<node id>` gets `{port, choice, confidence, sure}` (`choice` is its best
  guess when it went along `unsure`): `{{ steps.n4.choice }}`.
- The call is made inside the run's activity scope, so it shows in the activity ledger under the automation and
  the run, and counts towards budgets. An answer is kept for 7 days per activity and question (in
  `automation_state`), so a retry or a dry run (Try it) of the same activity doesn't pay again; a dry run does call
  the model the first time. No model is called unless an automation has a decide node, and then only for activities
  that reach it.

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
| `enhance` | [enhances](enhance.md) an image `asset` whatever the settings, keeping the original, and reads it again from the copy | lens | `{job, asset}` |
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
activity's page as what it was asked from, so you can open it, approve what it proposed there and carry on. Model
calls count in the activity ledger under the run, and stop once a budget they count for (the automation's, its
namespaces', or everything's) is used up.

Params marked `"format": "prompt"` in an action's schema (`ask_model`'s `prompt`, `ask_agent`'s `instructions`) are
prompts: they are rendered with `variables.prompt` ([Variables and templates](#prompts)), which marks as data
(`<untrusted-…>`) what the run read from outside Lens before the model sees it: each activity's `lens.untrusted`
fields (an email's subject and body), everything earlier steps passed on (a model's reply, a web page) and saved
variables. Your own words in the prompt stay as written. A prompt param may name a saved prompt template instead of
its text. `ask_agent` passes the activity it is about the same way.

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

## Approvals

Built: everything that waits for your pick is an approval in one table (`app/domain/approvals.py`): the assistant's
proposals in a conversation, as before, and an automation's actions that ask first. When an action node's intent asks
(its effect is `out`, or `lens` on anything but the event's own object), the engine hands it to
`approvals.ask(db, cfg, intent, event=None)`, which keeps:

| Field | |
| --- | --- |
| `origin` | `{automation, run, node}`: the run that asked (null for the assistant's own approvals) |
| `intent` | the intent, with the params as the run filled them in |
| `summary`, `automation`, `why` | what it would do (the action's `describe`), the automation's name, and the activity that caused it (`{activity, kind, summary, published, url}`) |
| `account`, `space` | the automation's person, whose pick it is, and the namespace it is about |
| `expires_at` | when it expires: `automations.approval_days` (7) after it asked |
| `told` | the chats you were told in |
| `status` | `pending` (waiting), `running`, `done`, `failed`, `declined` or `expired`; `decided_by`, `decided_at`, `result`, `error` |

You are told once, in your own chat app when one is linked ([Telling a person](#telling-a-person)): "School emails
wants to post in the Matterbridge gateway family: “Trip on Friday”. Open <link> to decide." The link opens the approval
in Lens. Handing the same intent over again gives the same approval. At most 100 wait for one automation: past that its
intents are dropped, and the run says so, so a flood of activities can't bury your inbox or your chat.

Deciding (`POST /api/v1/approvals/{aid}`):

- `approve` carries the intent out through `actions.perform`, for its person and as the automation (what it writes is
  by `automation:<id>`, model calls count in the activity ledger under the run, and what it causes carries the run as
  its cause). Its outcome, `done` with what it gave back or `failed` with why, goes on the approval and on the run.
- `decline` does nothing, and the run says it was declined.
- `edit` with `params` changes them first (a param given as null is left out), checked against the action's schema;
  what the run filled in from a template stays as it is now. A credential header shows as `••••`, and given back as
  `••••` it keeps its sealed value.
- `always` approves it, then lets that action node run without asking from now on (the automation's policy, `auto`).
  Only an action that reaches outside Lens or other people asks each time, so only those can be allowed.

Approving, editing and `always` are yours: signed in to Lens, on the approval's card, or approving with a yes in your
own chat app (below). An API key, an app, MCP and the assistant can list and decline approvals, never approve them,
since approving is the person's pick; each approval's `can` says what the caller may do with it. This is the one
place where an agent can't do what you can: agents read them with `approvals` and decline with `decline_approval`
(over [MCP](mcp.md#tools) and in the [assistant](assistant.md#the-assistants-tools)), and there is no tool to approve.
The assistant's own approvals (sending mail, adding an event, writing a file, changing entities and the rest) follow
the same rule: you approve them signed in to Lens or in the chat they were asked in, and an API key, an app, MCP and
the assistant can only decline them (403). A research plan is the one exception, and only when an admin turns on
`research.agents_skip_review` ([Deep research](assistant.md#deep-research)).

**In your chat app.** A reply of "yes" in the private chat you were told in approves an automation's action, under
the rules a chat room's approvals follow ([Chat rooms](chat-rooms.md#what-it-does)), and only when all of these hold:

- the action changes something inside Lens (effect `lens`); one that reaches outside Lens or other people is
  approved on its card in Lens, and the reply says where;
- the channel lets you approve there (approving in the room is on, and you are on its list of people who may talk to
  Lens);
- it is the only thing waiting in that chat: with several, a bare "yes" lists them and "yes 12" picks one ("yes all"
  leaves an automation's actions waiting, to be picked one at a time).

"No" declines it, whatever it does. The message that told you says "Reply “yes” to do it or “no” not to" only when a
yes there would count; otherwise it gives the link.

Undecided approvals expire after `automations.approval_days` (Settings → Automations, 1–365, default 7): a sweep
called from the scheduler once an hour marks them `expired`, nothing runs, and the run says so. The assistant's own
approvals don't expire.

Each approval is an activity too: `approval.waiting` when it asks and `approval.decided` when it is decided or
expires, seen by its person alone ([Approvals as activities](#approvals-as-activities)). A namespace's notification
target can send `approval.waiting` to a team chat or webhook ([Notifications](notifications.md#events)); it is off
until a target asks for it.

**The Approvals page** (`/approvals`, or ⌘K (Ctrl+K) → Approvals) lists what waits for your pick, each automation's
together and the assistant's by conversation: what it would do, why (a link to the activity that caused it), whether
it reaches outside Lens, when it expires, and its params. The first is in focus, so Enter approves it and focus moves
to the next; Tab moves between them. **Edit** changes the params in place (Enter approves with the changes),
**Always allow** shows where the node can be allowed, and **Decline** takes it out at once with Undo on the notice
for a few seconds instead of a question. The link you were told (`/approvals?id=12`) brings that one first, or says
what became of it. Below, what was decided lately. While anything waits, a badge in the top bar counts it, the nav
has an Approvals item under Automate with the same count, and Home's Needs attention has a row for them; with nothing
waiting, none of them shows, so the nav stays as short as it was.

The inbox reads `GET /api/v1/approvals` (newest first; `status`, `origin=automation|chat`, `automation`, `chat_id`,
and `before` with `limit` to page), `GET /api/v1/approvals/{aid}` and `GET /api/v1/approvals/count` (`{waiting,
automations}`, for a badge).

## Time

Time is a trigger and a step: "every weekday at 7:00", "at 9:00 on the 20th", "30 minutes before a calendar event",
"wait 2 hours, then...", and the clock behind a join's "if no reply within 2 days". All of it rests on timers.

Every automation's schedule and at trigger is also a row on the [Schedules](schedules.md) page, next to the routines
and the backup, where it is switched, re-timed and run now.

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

Built as timers: saving an automation sets them, and when they fire they start a run
([Running automations](#running-automations)). Each trigger checks as
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

### Actions that wait

Built as timers; the automation's run hands them over. An action node with a `delay`
(`2h`) or a `when` (a time, or a template giving one) makes an intent with that time and status `scheduled`, and
`timers.later(db, intent)` keeps it as the timer `intent-<intent key>` (its run and node, like `intent-51-n7`), giving
back `{timer, at}` to keep with the intent. The run's page shows it as "scheduled for 18:22".

When its time comes, its risk is worked out again (`timers.risk_now`) from the automation as it is then, not as it
was when the run made the intent:

- `read` and `self` run on their own.
- `lens` runs on its own only if it did when the run made it (the event's own object) and its namespaces are still
  the automation's.
- `out` runs on its own only while the person still has that node set to run on its own; otherwise it asks.
- The action's own check may only make it stricter. The effect is the stricter of the intent's and what its params
  give now.

A routine one is carried out through `actions.perform` as the automation (its person's permissions as they are then),
inside its run's cause (what it makes carries `lens.cause`, for the loop guard) and its ledger scope
(`automation:<id>`, `automation_run:<run>`). One that should ask becomes an approval
([`approvals.ask`](#approvals)). The run hears of each step through `automations.update_intent`: done
with what it gave back, failed with why, waiting, or expired when the automation was switched off or removed first,
the timer was cancelled, or Lens was off past the catch-up window.

## Variables and templates

Built (`app/domain/variables.py`). An action's params and prompts are templates over the [run
context](#run-context): sandboxed Jinja, the same sandbox as Lens's other templates (`templates.py`: no reach into
Python, no huge strings, a 2 MB cap on what one renders). A template reads everything the run knows, so one line can
join two sources: `{{ events.mail.object.subject }} on {{ events.soon.object.startTime | date('%a %H:%M') }}`.

```python
variables.context(db, cfg, automation, run, trigger, event, events, user, now=None)  # the run context
variables.render(value, ctx, untrusted_paths=None, where="")    # params, dicts and lists walked
variables.prompt(template, ctx, untrusted_paths=None, cfg=None, db=None)  # {text, rules, system, schema, template}
```

### The run context

The keys are those of [Run context](#run-context). `now` is when the run started in UTC (`2026-10-15T16:22:09Z`) and
`now_local` the same moment in the run's time zone (`2026-10-15T17:22:09+01:00`). The time zone is the trigger's
(a schedule's or an `at`'s), else the first one the automation's triggers name, else UTC. `namespace` is the event's
namespace when the automation works there, else its first.

### Params

- A param that is exactly one expression keeps its type: `"{{ steps.n3.count }}"` gives the number 3,
  `"{{ event.object.from }}"` a list, `"{{ event }}"` the whole activity (a copy). Anything else renders as text.
- Dicts and lists are walked, so a param such as `{"to": ["{{ user.email }}"]}` is filled in where it stands.
- `a.b` reads a dict's key `b` first, then a method of that name, so an action's output `{"items": [...]}` reads as
  `steps.n3.items` (Lens's other templates keep Jinja's own order).
- An error names the param and the variable that isn't there, with the nearest name there is, and the template's
  line: `text: events.mail.object.subjct is not defined; did you mean subject?`, or `to: user.zzz is not defined; it
  has email, id, name`. A path guarded by `default` or `is defined` doesn't count.
- Text from outside Lens keeps its words in params (a message forwards the subject as it was written), without the
  invisible characters that would smuggle instructions to a model that reads it later. The intent still says it
  read outside text (`untrusted`), and outbound actions treat it so.

### Prompts

A prompt (a param with `"format": "prompt"`) renders with every value read from outside Lens marked as data with
`untrusted.wrap`: the source is the activity's kind and actor (`email.received from Work Gmail`), and anything that
looks like a marker or a chat model's control token is broken up. That covers each activity's `lens.untrusted`
fields, everything earlier nodes passed on (`steps`) and the [saved variables](#saved-variables) a run set from
outside text. The person's own words, Lens's own fields (a summary, ids, times) and numbers stay as written.
Filters and string methods work on the words of marked text and give back marked text, so `| plain | truncate(4000)`
or `.upper()` can't drop or cut the markers; comparisons (`==`, `in`, `length`) read the words themselves, so `{% if
event.object.subject == "Trip" %}` works as written.
`rules` is the system prompt's paragraph about marked text, to add to the system prompt (the AI actions add it
themselves); with `ai.untrusted_marking` off nothing is marked and `rules` is empty.

A prompt param may name a saved prompt template (Settings → Templates, `POST /api/v1/templates`) instead of its text:
`{"template": 5}` for its current version, `{"template": 5, "version": 2}` to pin one. It is checked when the
workflow is saved (the template must be there and be a prompt template). The template's own instructions and output
schema fill `ask_model`'s `system` and `json_schema` when the node doesn't give them, so the same prompt serves
pipelines and automations.

### Filters

Every template in Lens has these, on top of Jinja's own (`truncate`, `first`, `join`, `default`...) and Lens's `json`
and `tc`:

| Filter | Gives |
| --- | --- |
| `date(fmt, tz=None)` | a time written with a `strftime` format, in `tz` (default the run's time zone): <code>{{ x &#124; date('%a %d %b, %H:%M') }}</code> |
| `local` | a time in the run's time zone, as ISO 8601 with its offset |
| `ago` | how long ago a time was, from the run's `now`: `3 hours ago`, `in 2 days`, `just now` |
| `plain` | HTML as plain text, a line per block |
| `emails` | the email addresses in text (or a list of texts), lowercased, each once |
| `defang` | web addresses broken up so a chat app doesn't fetch them (`hxxps://evil[.]example/`) |

Times may be ISO 8601 text (a `Z`, an offset, or none for UTC; a date alone stays a date), epoch seconds or
milliseconds.

### Examples

A message joining two triggers (a `notify` node's `text`):

```text
{{ events.mail.object.subject }} from {{ events.mail.object.from | emails | first }}: you have
{{ events.soon.object.name }} {{ events.soon.object.startTime | ago }} ({{ events.soon.object.startTime | date('%a %H:%M') }}).
```

A prompt with outside text marked (`ask_model`'s `prompt`, with a `json_schema` asking for `{trip, date}`):

```text
Does this email from {{ event.object.from | join(', ') }} ask us to sign anything for a school trip?
Subject: {{ event.object.subject }}
{{ event.object.content | plain | truncate(4000) }}
```

The model sees the subject and the body between `<untrusted-…>` markers, with the rules that say marked text is
material to read, not instructions to follow; your question stays as written.

A counter (a `set_variable` node `count`, then a `notify` node after it):

```json
{"action": "set_variable", "params": {"name": "door_opened", "op": "increment"}}
```

```text
The front door has opened {{ steps.count.value }} times.
```

### Saved variables

An automation keeps values between runs as `vars.<name>`: a counter, when it last alerted, the last ten senders. Each
is the automation's own, or its namespace's, which every automation working there reads (an automation's own wins on
a name). A run reads them as they were when it started; a `set_variable` node changes them for the runs after it and
gives what it set as `steps.<node>.value` (and `.previous`) for the nodes after it in the same run.

The `set_variable` action (group Run, routine: it changes nothing outside the automation) takes:

| Param | |
| --- | --- |
| `name` | lowercase letters, digits and `_`, starting with a letter, up to 40 |
| `op` | `set` (default) to `value`, `increment` by `value` (a number, default 1), `append` `value` to a list of the last 100, or `clear` |
| `value` | any JSON up to 16 KB, often a template: <code>"{{ event.object.from &#124; emails &#124; first }}"</code> |
| `scope` | `automation` (default) or `namespace` |
| `namespace` | for `scope: namespace`, its id; the automation's only namespace when left out. Changing a namespace's variables needs the automation's owner to be an editor there |

Two runs changing one variable at once both count: a write that loses the race reads the value again (up to five
times). A scope keeps up to 100 variables. A variable set from a template that reads what the run got from outside
Lens (its activities, earlier steps or such variables) is marked so, and prompts then wrap it as outside text; other
variables stay as written. People change them with
`GET`/`PUT /api/v1/automations/{id}/vars` and `/api/v1/namespaces/{name}/automation-vars` ([API](api.md#automations)),
and agents with the `automation_vars` and `set_automation_vars` MCP tools.

### The variable picker and preview

Both work on an automation as it is being written (a draft: `{triggers, join?, workflow?, spaces?, id?, name?}`,
with `workflow` a saved workflow's id or a graph, which may be unfinished), so the builder can help before anything
is saved:

```python
variables.catalog(db, cfg, draft, node=None, user=None)   # [{path, type, label, example, group, untrusted}]
variables.preview(db, cfg, template, draft, user, activity=None, trigger=None, node=None, as_prompt=False)
```

`POST /api/v1/automations/variables {draft, node?}` lists what a template there can read, for the picker that opens
on `{{`:

- the run context's keys and their fields (`user.email`, `namespace.name`, `now`...);
- each trigger's activity, `events.<as>.<field>`, from its kind's schema (a schedule's or an `at`'s is the clock's
  `clock.tick`, a webhook's `webhook.received`); `event.<field>` for the fields all the triggers' kinds share; a
  batch's activities as a list (`events.mail.0.object.subject`);
- the saved variables there are (the automation's own when the draft has its `id`, and its namespaces');
- with `node`, what the action and decide nodes before it pass on, `steps.<id>.<field>`, from each action's output
  schema (an `ask_model` with a `json_schema` gives the object it describes).

Each has its type, a label from the schema's titles, an example (from the kind's sample, or the variable's value),
a `group` for the picker's headings and `untrusted` for text from outside Lens, which prompts mark as data.

`POST /api/v1/automations/preview {template, draft, activity?, trigger?, node?, prompt?}` shows what a template gives
before it is saved: against a real activity by id (404 for one you can't see) or the kind's sample of the trigger
that fires (`trigger`, by its `as`; default the first). A param's `value` keeps its type and `text` is it written
out; with `prompt` (or a saved prompt template, `{"template": 5}`) the text is what the model will see, outside text
marked, with `rules`. The steps before `node` are stand-ins named by their path (`[steps.ask.trip]`), as a preview
runs no node; a run's real values come from a test run. A template that fails answers with `error` and `line`, like
`text: events.mail.object.subjct is not defined; did you mean subject?`; a draft that doesn't check answers 400.
Anyone signed in may use both, with their own namespaces (none named: theirs) and only an automation they may change.
Agents have the `automation_variables` and `preview_template` MCP tools.

## Saving automations

Built (`app/domain/automations.py`, `POST /api/v1/automations`). An automation acts for the
person who made it (`run_as`), with their permissions, in namespaces they can edit:

```json
{"name": "Say new assets", "triggers": [{"as": "new", "on": "event", "kinds": ["asset.added"]}],
 "if": {"all": [{"path": "event.object.media", "op": "equals", "value": "audio"}]},
 "graph": {"nodes": [{"id": "i", "type": "input", "config": {}},
                     {"id": "a", "type": "action", "config": {"action": "log_line",
                                                              "params": {"text": "New: {{ event.object.name }}"}}}],
           "edges": [{"source": "i", "target": "a"}]}}
```

- **Namespaces** (`spaces`): ones its person can edit. Left out, it is each namespace they can edit; an admin's
  works in every namespace (`null`), now and to come.
- **The Then** is a `graph` ([The event workflow scope](#the-event-workflow-scope)), saved as a workflow of scope
  `event` named after the automation, or a saved event workflow (`workflow`, with `version`, or none to follow the
  published one). Saving a new graph saves a new version of its workflow, which it then follows.
- **Credentials** in an `http_request` node's headers ([Calling a web address](#calling-a-web-address)) are sealed
  when its graph is saved, with the key of its first namespace (the first there is, for one that works everywhere),
  bound to the automation and to the node's `url`. A value holding `{{ }}` is filled in when it runs and stays as
  written, and a credential needs the `url` written out, not a template. Reading the automation back gives the sealed
  value, and saving that again keeps it.
- **Checks** when it is saved: the triggers, the join and the If as in [the contract](#automations-triggers-and-joins),
  the graph, the namespaces, `limits` (`cooldown`, like `10m`, default `0s`; `max_per_hour`, 1 to 3600, default 60)
  and the policy. Saving sets the timers of its `schedule` and `at` triggers ([Schedule, at and relative
  triggers](#schedule-at-and-relative-triggers)), and switching it off or deleting it calls them off, with any its
  joins set.
- **Webhook triggers** get their address when the automation is saved ([Webhook trigger](#webhook-trigger)): the
  answer's `hooks` gives each new one's `url` and signing `secret`, shown this once, and the trigger keeps the hook's
  id. Saving it again keeps its address; a webhook trigger dropped (or changed to another kind) loses it at once.
- **Switching** (`POST /automations/{id}/enable {"on": true}`) records when it was last switched on (`enabled_at`):
  it reacts only to what happens after that, so switching one on never replays old activity. New automations are on
  unless `enabled: false`.
- **Deleting** switches it off and hides it; `POST /automations/{id}/restore` brings it back, switched off. After 30
  days a deleted automation is removed for good, with its runs and its workflow (unless another automation runs it).
- **The policy** (`{action node id: ask or auto}`): a node whose action reaches outside Lens or other
  people, or changes something in Lens other than the event's own object, asks first. Only the person,
  signed in to the web app, lets a node run on its own (`POST /automations/{id}/policy {"node": "a", "value":
  "auto"}`); API keys, apps and agents get a 403, and saving through any other route refuses `auto` (a node already
  on `auto` may stay so). A node that leaves the graph takes its pick with it.
- **Who sees it**: admins see and change every automation; anyone else the ones that work only in namespaces they
  can edit.

`GET /api/v1/automations/actions` lists what an action node can do: each action's params (a JSON Schema), its effect
and what it gives back.

### Describe it in words

Built (`app/domain/automation_draft.py`). `POST /api/v1/automations/draft {"text": "when a recording is added to pods,
log its title", "spaces": [3]}` asks the model once to turn the sentence into a draft, with the kinds of activity and
their fields, the [match operators](#match-trees) and the [actions](#actions-and-intents) as its vocabulary. The answer
is `{draft, dropped, unmapped}`:

- `draft` is what `POST /automations` takes, switched off: its name, triggers (on activities, a webhook, whose
  address comes when it is saved, by hand, or in time: a schedule, a time, or a time in an activity such as a
  meeting's start, in UTC unless the sentence names a zone), If and a graph with its actions in order (an action that may reach
  outside Lens hangs off the one before it, as [the leaf rule](#the-event-workflow-scope) has it).
- `dropped` says what in the model's draft didn't fit and why: a trigger, its `where`, the If, an action that doesn't
  exist or params that don't fit its schema. With no trigger left it runs when you run it; with no action left it has
  no Then yet.
- `unmapped` lists what the sentence asked for that it couldn't express, such as "when both arrive" (a draft
  doesn't make joins yet; add one before saving).

Nothing is saved: try the draft (`POST /automations/test`), change it, then save it. The call counts in the [activity
ledger](activity.md) for you and its namespaces, and isn't made once a budget they count for, or everything's, is
used up.

### Starters

Built (`app/domain/automation_starters.py`). `GET /api/v1/automations/starters` lists ready-made drafts, each
`{id, name, description, draft}`, with `draft` what `POST /automations` takes, switched off. Nothing is stored: a
starter is saved only when someone saves it, and runs only once it is turned on. The description says what to change
first, such as the school's address. In the web app they are under **Describe it in words** on a new automation.

| Starter | What it does |
| --- | --- |
| Tell me about school emails | An email from an address with “school” in it tells you in your chat app; posting it to the `family` Matterbridge room asks you first |
| Router alerts | An alert in a sensor's log whose name has “router” in it tells you, at most once an hour |
| Morning digest | Every weekday at 07:00 (UTC until you change it) the agent sums up yesterday's timeline items you haven't reviewed, then tells you |
| No reply in two days | An email whose start asks something, with no email in your Sent mailbox within two days, reminds you; any email you send in that time calls it off |
| Webhook to note | A call to its webhook writes a note with what was sent |

### Agents

Built. Over [MCP](mcp.md) an agent uses `automations` (list them, read one with its latest runs, or one run),
`automation_actions`, `save_automation`, `test_automation` and `run_automation`, with the same checks as the API; a
body that doesn't fit comes back in plain words, naming the field. A new automation is saved switched off unless the
agent says `enabled: true`, and saving and running need a token with the write scope.

The [assistant](assistant.md) has the same tools. Saving there is made at once and always switched off; switching one
on, or running one now, becomes an approval card the person decides, which says what it listens for and the actions
it takes. Switching one off is made at once. Neither MCP nor the assistant lets a node run on its own (`auto`): only the
person, signed in to the web app, does.

## Running automations

Built (`app/domain/automations.py`). A thread named `automations` runs where the notifier and routines do (the API's
background work and `lens worker`). It follows the stream ([Following the stream](#following-the-stream)) for the
kinds that switched-on automations' triggers listen for, every `events.poll_seconds` and at once when something is
emitted or an automation is saved. With no automation switched on (or automations switched off in Settings), it only
moves its place to now, so nothing replays when one is switched on.

Which triggers listen comes from `match.compile_triggers(automations)`, an index of the switched-on automations'
stream triggers (`event`, `relative`, and `webhook` for `webhook.received`): `kinds` (every kind they listen for, to
follow), `by_kind` (for each kind, the kinds under the ones named included, the triggers listening, as `{automation,
as, trigger, row}`; `match.candidates(index, activity)` reads it) and `hint`. For each source whose every listening
trigger names the actors it takes (`actor.id` `equals` or `in` in its `where`), the hint gives that source
`{"actors": [...]}`, so a busy source, such as sensor readings, is read only for the sensors someone listens to.

Each new activity is checked against each trigger that listens for its kind (a webhook trigger listens for
`webhook.received` calls to its own hook), and skipped when:

- it happened before the automation was last switched on (`enabled_at`);
- an automation made it, unless the trigger says `from_automations`;
- it is the end of a chain of automations setting each other off as long as `automations.max_depth` (3);
- it isn't in one of the automation's namespaces, or the automation's person may not see it;
- the trigger's `where` doesn't match it (in the trigger's time zone; a `changed` in it compares with the last
  activity the trigger saw).

Then it is claimed (for the automation, trigger and activity together), so it runs each automation once, even with the
API and a worker both following. **`automations.fire(db, cfg, automation_id, alias, activity, events=None, dry=False)`**
is the one way every trigger starts a run (the stream with its webhook calls, run now, the clock, timers and joins);
with a join other than `any`, the claimed activity goes to the join first ([Joins](#joins)) and the automation fires
when it completes. It gives the run's id, or none when nothing ran. A run goes:

1. **Its person**: if the person it acts for lost their account, their admin role (for one that works everywhere) or
   the right to edit one of its namespaces, the run fails saying so, and the automation is switched off.
2. **The If**, on the [run context](#run-context), with the run's clock and time zone: a `seen` in it looks back in
   the automation's namespaces as its person may see, and `changed` compares with what the automation saw last
   ([Operators](#operators)). The If and the graph's condition, filter and switch nodes share one State a run, so a
   node testing what the If tested agrees with it; a dry run keeps nothing. When it is false the run stops there: it
   counts in `counters.skipped`, uses up no limit and makes no run and no activity.
3. **Limits**: the automation's `cooldown` (the wait between runs) and `max_per_hour`, then every automation's runs
   together this minute (`automations.max_runs_per_minute`, 60). Over one, nothing runs: it counts in
   `counters.limited` and says so in the stream (`automation.limited`) at most once an hour.
4. **Budgets** on the automation, its workflow and its namespaces ([Budgets](budgets.md)): over one, the run is held
   for your pick (status `held`, one at a time; later ones add to its `hold.missed`) or skipped, as its budget says.
5. **The graph** runs as its person, counted for `automation:<id>` and its run in the [activity ledger](activity.md):
   graph changes are marked as made by the automation, and activities it makes carry the run as their cause.
   Routine actions are done; ones that ask first wait for the person's pick and ones with a time wait for it (until
   approvals and timers are built, they stay waiting).
6. **It finishes** `done`, `partly` (some actions failed), `failed` or `waiting` (actions wait), with its cost and
   tokens, what each node did (`trace`: `done`, `skipped`, `failed`, or `waiting` and `scheduled` for an action that
   asks first or waits for its time), its log (at most 500 lines) and an `automation.<status>` row in the activity
   ledger; and it appears in the stream as `automation.ran`. When a waiting action is decided or its time comes, its
   status changes (`automations.update_intent`) and the run's follows.

Runs are kept for `automations.keep_runs_days` (30); each automation keeps its last 20 however old they are, and a
held run until you pick.

**Over the API** (`/api/v1/automations`; admins see every automation, anyone else the ones in namespaces they edit):

- `GET /automations` lists them with their counters, last run, actions waiting and cost this month, and for the
  list in the studio: `reads`, what sets each one off in words (`match.describe_triggers`: “Email received”, where
  the subject contains “trip”; every weekday at 07:00 (Europe/London)), `runs_today` (since midnight UTC, tries left
  out) and `next_at`, its soonest schedule, at, relative or join timer.
- `GET /automations/{id}/runs?before=<run>` pages its runs, newest first; `GET /automations/runs/{run}` gives one with
  its trace, log and what its actions did (`intents`); `GET /automations/runs?activity=<id>` the runs an activity
  started.
- `POST /automations/{id}/test` tries a saved one, and `POST /automations/test {"draft": {...}}` one you haven't saved,
  on an activity (`"activity": "<id>"`) or a kind's sample (`"kind"`, default its first trigger's): whether its If
  passes and what each action would do. Nothing is kept, sent or counted, and limits and budgets aren't checked.
- `POST /automations/{id}/run` runs it now, on an activity you name (in its namespaces, one its person can see), else
  the newest one its triggers listen for, else as started by hand. Limits, budgets and asking first apply.
- `POST /automations/runs/{run}/decide {"run": true}` runs a held run now, once, whatever its budgets say;
  `{"run": false}` skips it.
- `GET /automations/{id}/upcoming` lists when it runs next (empty until schedules are built).

## Refine later

- A draft from a sentence that makes joins ("when both arrive", "if no reply within 2 days").
- Switching an automation off and on keeps what an `all` or `count` join was waiting on, until its window passes; a
  changed join keeps the old one's rows until they expire.

- Variables inside match values (`{"var": "vars.threshold"}`), once saved variables are built.
- Statistical conditions, such as a reading above its 7-day average.
- Sequences longer than two steps ("A, then B, then C").
- The canvas shows and edits match trees on condition, filter and switch nodes (today it edits one test; a tree
  saved through the API is kept).
- A `?format=as2` JSON-LD view and per-actor outboxes for ActivityPub; SurrealDB `DEFINE EVENT` for Update verbs that
  no row records; pushing instead of polling.
- People given only some collections of a namespace see that namespace's activities about those collections.
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
- Variables and templates: secrets as variables (sealed, never rendered into prompts); typed variable declarations
  with defaults per automation; a time zone of each person's own, rather than the triggers'; wrapping outside text
  the same way in `tool_nodes.ask_model` (pipeline workflows) and extensions' prompt tools; an activity when a person changes a variable; marking a variable
  from outside text only when the value itself came from it, not whenever the run read some; removing an
  automation's variables when it is deleted (M6); a preview that runs the nodes before the template's (dry) rather
  than standing in for their outputs; the picker listing a saved prompt template's output schema for the node that
  uses it; matching on variables in triggers and the If (M5).
- The model and the agent: per-node model picks in the studio; streaming an agent's steps into the run's log as they
  happen; a namespace assistant's memory for automations (`remember` is left out today); approving an agent's
  proposals from the chat app it told you in.
- Calling a web address: secrets in the address itself (a Slack or Discord webhook URL) sealed like headers; PATCH and
  DELETE (DELETE would count as deleting); OAuth sign-in for an API; form-encoded bodies; retries with backoff;
  networks allowed for automations apart from notifications.
- Time: an action that waits shown in the person's time zone in `upcoming` (today UTC); calendar-aware schedules ("first workday of the month", holidays); sunrise and sunset triggers; an all-day
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
- Deleted automations kept for a time, then removed with their runs (today they stay until restored).
- The studio: dragging actions and rows to reorder them (today the arrows move an action); sharing an automation as a
  file; a builder laid out for phones first.
- Starters: "No reply in two days" matching the reply to the same conversation (emails carry no thread yet, so any
  email you send calls it off); a starter picking its sensor, room or address from what is connected, instead of a
  word to change.
- Credentials in the headers of a saved event workflow an automation follows (`workflow`) sealed too (today only a
  graph saved with the automation is; the workflow's are sent as written).
- Webhook triggers: a trigger option for a hook that takes only signed calls (`token_for(..., signed=True)`), and a
  new address for a trigger without saving it again.
- Lens itself: a run held over budget, waiting for someone's pick, as an activity of its own; backups taken before
  upgrades or with `lens backup` (only those from Settings, the API and the schedule are activities today).
- Approvals: a cost estimate on actions that ask (a run on another asset could say what it would cost); approve
  buttons in Telegram and Slack (today a yes decides only actions inside Lens); approvals that need two people; one
  card for many alike ("approve all 12"); trying a failed one again; expiring an automation's approvals when it is
  removed; on the page, keys for decline and edit, live updates instead of reading again each minute, and the
  assistant's proposals shown with their estimates.
- Limits count in fixed hours and minutes; a sliding window would spread a burst at the turn of the hour.
- Tell the person when a run is held over budget or an automation goes over a limit (today it shows in its runs, its
  last status and the stream).
- Automations that listen for every kind (or hundreds of automations): an index of triggers by kind and `where`, so a
  look reads each activity once whatever the number of automations.
- Drafts from a sentence: the person's own time zone when the sentence names none (today UTC).
