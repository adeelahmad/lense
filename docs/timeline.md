# Timeline

The timeline is one place to see what comes in from every source, newest first, and review it: each person marks
what they have looked at reviewed, what matters important, and hides what they don't want to see again. It is the
stream of activities that [automations](automations.md) follow, read newest first with who may see what.

Status: in progress. The studio's timeline is built in steps; only what is merged is called built.

| Part | Status |
| --- | --- |
| The timeline API (`GET /api/v1/activities`), the MCP tool `timeline` and the assistant's `timeline` | built |
| Review marks: reviewed, important and hidden, per person; the filters on them; `mark_activities` for agents | built |
| The Timeline page under Explore: filters, grouping, review with keys, new items as they come | built |
| A Latest card on Home with the newest unreviewed items | built |
| What each item caused (automation runs, approvals), and Approve and Decline on waiting approvals | planned |

## What shows

Every activity of the namespaces you can read, newest first, from every source the stream has (new assets today;
more kinds as the [automations](automations.md#kinds) modules add them). Activities meant for one person (their own
web-app chat) show to them alone, and those for admins to admins. Noisy kinds, like sensor readings, are left out
unless you ask for them by kind.

## The page

**Timeline** under Explore in the navigation, for everyone (members see their namespaces).

- Newest first. **Older** reads the next page. While the tab is visible the page looks for new items every 15
  seconds; when there are some, a **3 new** pill at the top brings them in, so the list never jumps under you.
- A run of the same source or person and the same kind, each within 30 minutes of the one before, is one line
  ("Work Gmail received 37 emails"), so a first mailbox sync reads as one line. A click opens it to its items.
- Each line has an icon by kind, the summary (a link to what it is about), who did it, the namespace, the kind and
  how long ago. Opened, an item shows its object's fields, the automation run that caused it if one did, **Open** and
  **Hide**.
- Filters: words in the summary (Enter applies), **Unreviewed only**, **Important**, kinds by family (Email,
  Calendar, Files, Notes, Sensors, Chat, Automations, System; noisy kinds like sensor readings only when picked),
  source or person (from those on the timeline), namespace, entity (as activities tag it), a date range, and an
  automation (what it did or caused). Filters live in the address, so a view can be linked, and the last set you used
  comes back the next time you open the page in this browser. **Clear** drops them all.
- On a phone the list is one column and the filters open in a sheet from **Filters**.

### Reviewing

| Key | Does |
| --- | --- |
| `j` / `k` | move to the next or previous line |
| `Enter` | open or close the line |
| `r` | mark it reviewed, or not |
| `i` | mark it important, or not |

Hovering or focusing a line shows its **Important** (star) and **Reviewed** (circle) toggles; once set they stay in
sight. On a line of several items they mark every item. **Mark all shown as reviewed** marks everything on the page
you haven't reviewed, with **Undo** in the message rather than a confirm; **Hide** works the same way.

### Home

The overview on Home has a **Latest** card with the five newest activities you haven't reviewed and a link to the
timeline with **Unreviewed only** on.

## Review marks

Each person marks activities for themselves alone; nobody else sees or sets them, admins included.

| Mark | Means |
| --- | --- |
| `reviewed` | you have looked at it; "Unreviewed only" leaves it out |
| `important` | it matters; filter by it to find it again |
| `hidden` | you don't want to see it; the timeline page leaves hidden activities out |

Marks change nothing else: an activity you hid still triggers automations and still shows to everyone else.

- `POST /api/v1/activities/{id}/mark` with `{"reviewed": true}` (any of the three, each true or false; one left out
  stays as it is) sets your marks on one activity and answers them: `{reviewed, important, hidden, at}`. An activity
  there is none of, or that you can't see, is 404.
- `POST /api/v1/activities/mark` with `{"ids": [...], "reviewed": true}` marks up to 500 at once and answers
  `{marks, unknown}`: your marks now by id, and the ids there are none of or you can't see.
- `GET /api/v1/activities` has your marks on the page in `marks` (by id, only those you marked) and filters by them:
  `reviewed=false` is "unreviewed only", `important=true` what you marked important, `hidden=false` leaves out what you
  hid. A filtered page reads on until it is full, as the other filters do.
- Setting a mark needs a signed-in session or an API key with the write scope; a read-only key reads them.

## Agents

- MCP: `timeline` takes `unreviewed: true` for what you haven't marked reviewed and has your marks in `marks`;
  `mark_activities` marks activities by id (`ids`, and `reviewed`, `important` or `hidden`), for the person it acts
  for, with a token that has the write scope ([MCP](mcp.md)).
- The assistant: `timeline` takes `unreviewed` and lists each activity's marks; `mark_activities` marks them for the
  person. After it has read text from outside Lens (the timeline's own titles count), hiding or marking reviewed
  waits for the person's yes, since that text may have asked for it; marking important or bringing things back acts
  at once ([Chat](assistant.md)).

## How marks are kept

One `event_mark` row per person and activity, keyed by a hash of the two, with the three flags and when they were
last set. A row with every flag cleared is deleted, so the table holds only what someone marked. Marks are read for a
whole page of activities in one query, and the mark filters thin each page a source gives out in one query too. They
go when the person is removed.

## Refine later

- Server-sent updates instead of the page polling for new items.
- Saved filter views.
- A daily digest of unreviewed items, as an automation starter.
- Marks on activities whose rows are gone (an event log entry past `events.keep_days`, a deleted asset) stay until
  the person is removed; a tidy could drop them.
