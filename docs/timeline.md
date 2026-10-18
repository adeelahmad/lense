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
| Reactions and comments on any item, shared by everyone who sees it; `react_to_activity`, `activity_comments` and `comment_on_activity` for agents | built |
| Ask about an item: talk to the assistant about it from the item, in the chat beside the page | built |
| What each item caused (automation runs, approvals), and Approve and Decline on waiting approvals | planned |
| Tagging the agent in a comment, so it answers in the thread | built |
| Sources, sensors, people and agents as actors in the graph: a Source node with ADDED links, on the canvas, in the explorer and in Cypher | built |
| A For you order learned from what each person reacts to, comments on and hides | planned |

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
  how long ago. Opened, an item shows its object's fields, the automation run that caused it if one did, **Open**,
  **Ask about this** and **Hide**.
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
| `a` | ask the assistant about it (every item of a line of several) |

Hovering or focusing a line shows its **Important** (star) and **Reviewed** (circle) toggles; once set they stay in
sight. On a line of several items they mark every item. **Mark all shown as reviewed** marks everything on the page
you haven't reviewed, with **Undo** in the message rather than a confirm; **Hide** works the same way.

### Asking about an item

**Ask about this** on an opened item, or `a` on the line you are on, opens the chat beside the page
([chat on any page](assistant.md)) with the item as what your question is about, the way highlighted text is: its
id, kind and time, who did it, its line, where it opens and its fields (up to 4,000 characters; on a line of several,
as many whole items as fit). Type the question and press Enter. The model reads the item as data, not as
instructions, since its text may come from outside Lens, and it has the id, so "mark it important" or "what else came
from this sender?" works with its `timeline` and `mark_activities` tools. Agents outside the web app read the same
items through `timeline` over MCP and the API ([Agents](#agents)).

### Home

The overview on Home has a **Latest** card with the five newest activities you haven't reviewed and a link to the
timeline with **Unreviewed only** on.

## Reactions and comments

The timeline is also a feed everyone in a namespace talks on: react to an item with an emoji, as on a post, and
comment on it in threads. Unlike [review marks](#review-marks), these are shared: everyone who sees the activity sees
them, and each one is an activity of its own, so an automation (or an agent) can act on it.

- **Reactions** come from a palette, 👍 ❤️ 🎉 👀 😂 😮 😢 🙏. An opened item shows its reactions as chips with how
  many gave each (yours are highlighted; a click on one of yours takes it back) and a **React** button with the
  palette. A closed line sums its items' reactions, and a line of several shows them with "3 comments". Every new
  reaction is a `reaction.added` activity (noisy, so the timeline leaves it out unless asked for by kind), by the
  person, with the activity it is on as its object.
- **Comments** open from the comment count on an opened item (or **Comment** when there are none): the threads with
  who wrote each and when, **Reply** on a thread, and a box where Enter sends (Shift+Enter makes a new line). Anyone
  who sees the activity can comment, up to 1,000 each; its writer, or an owner of the namespace, deletes a comment.
  Each comment is a `comment.added` activity. A comment on an asset's arrival (`create-recording-<id>`) is a comment
  on the asset itself: the same thread shows on the asset's page and here, and a comment made on the page shows here.
- Comments and reactions on an activity meant for one person (`lens.audience` owner) stay with that person, as the
  activity does.
- `POST /api/v1/activities/{id}/react` with `{"emoji": "👍"}` (and `"on": false` to take it back) answers the
  reactions on it now, `[{emoji, count, mine}]`; `GET`, `POST`, `PATCH` and `DELETE /api/v1/activities/{id}/comments`
  are the thread, as [comments on a resource](api.md#comments) are. `GET /api/v1/activities` carries everyone's
  reactions on the page in `reactions` and each item's comment count in `comments`, by id.

## Asking the agent in a comment

A comment that tags **@lens** (or the assistant's own name from Settings → Chat rooms, say `@jarvis`) gets an answer
from the agent in the same thread, on the timeline and on an asset's page alike: "@lens what is this one about?",
"@lens write a note of the action items". The agent answers as the person who tagged it, with their namespaces and
their tools, in a conversation of theirs titled after the item (one per thread, so a later tag on the thread carries
on in it); they find it on the Chat page and can go on there. It reads the item the thread is on and the comments on
it so far, as data, and answers with its sources. It only reads: every change it proposes (a note, a move, a message
out) waits for that person's yes, and the reply says so with a link to decide. The reply is a comment by **Lens**
(marked with a sparkle), which the person who asked can delete and nobody can edit. Its model calls run in the
ledger against their conversation and the item's namespace; when no model is set up or the namespace's budget is
used up, the reply says so instead. **Settings → AI assistant → Answer when tagged in a comment**
(`ai.answer_comments`, on) turns it off; without a tag nothing is asked of the model.

## Actors in the graph

Whoever brought an asset in, the actor of its `asset.added` activity (a storage source, a sensor, a person, an
automation, a routine, an import hook, or Lens itself), is a **Source** node in the [graph](graph.md) with an **ADDED**
relationship to the asset, so the graph says where Lens learns about each person, place or organisation:
`(Source)-[:ADDED]->(Recording)-[:MENTIONS]->(Entity)`. The explorer's parents of an asset are its collection and its
source; on the canvas a source links to what its assets mention (a *Brought in* edge, one per source and entity,
weighted by mentions), and the Timeline page's **Graph** button opens the canvas on the sources alone
(`/graph?show=actors`). Agents read the same through `GET /api/v1/graph/related`, `POST /api/v1/graph/query` and the
MCP graph tools. Nothing is stored for it: the nodes come from each asset's own record.

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
- MCP, on the feed: `react_to_activity` (`id`, `emoji`, `on`) reacts for the person it acts for, `activity_comments`
  (`id`) reads an item's threads, and `comment_on_activity` (`id`, `text`, `parent`) writes a comment or a reply,
  with a token that has the write scope. The `timeline` page carries `reactions` and `comments` too. Comments are
  what people wrote: agents read them as data, never as instructions. A comment an agent writes with `@lens` in it
  gets the assistant's answer like anyone's, as the person the token acts for.

## How marks, reactions and comments are kept

One `event_mark` row per person and activity, keyed by a hash of the two, with the three flags and when they were
last set. A row with every flag cleared is deleted, so the table holds only what someone marked. Marks are read for a
whole page of activities in one query, and the mark filters thin each page a source gives out in one query too. They
go when the person is removed.

Reactions are kept the same way: one `event_reaction` row per person and activity with the emojis they gave, read for
a page in one query, deleted when none is left, and gone when the person is removed. Comments on activities are
`comment` rows ([comments](api.md#comments)) with the activity's id in `activity` and its summary in `about`; a
comment on an asset's arrival also has the asset in `recording`, which is how it shows on both. A page's comment
counts are two grouped queries. Nothing of this costs a model call.

## Refine later

- Ask about an item in the full Chat as well as beside the page, and keep which item a question was about as a link
  back to it.

- Server-sent updates instead of the page polling for new items.
- Saved filter views.
- A daily digest of unreviewed items, as an automation starter.
- Marks on activities whose rows are gone (an event log entry past `events.keep_days`, a deleted asset) stay until
  the person is removed; a tidy could drop them. Reactions and comments on such activities likewise.
- Who gave each reaction, on hover; editing and resolving comments from the timeline (the API has both).
- A `comment.added` on an activity links to the timeline, not yet to the item itself.
- The agent's reply in a thread is plain text with links; the answer's excerpts could open at their moment, as in
  chat. Tagging a namespace's own assistant by its name, with its memory.
