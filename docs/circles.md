# Circles of trust

Your agent works from your own data. Circles are how it tells the people close to you what they need to know: your
partner and children, your parents, friends, colleagues. You put people in a circle on purpose, share something from
your timeline or your resources with a circle and a note, and each of them sees it in their own timeline. Both
timelines show it, so nothing is shared behind anyone's back, and no model is called to do it.

Status: in progress. Circles are built in steps; only what is merged is called built.

| Part | Status |
| --- | --- |
| Circles of people on the same Lens, sharing an activity or a resource with them and a note, with the item or without, undo; API, MCP and the assistant | built |
| Circles under Profile in the web app, and a Share action on timeline items and resource pages | built |
| Rules: an automation shares what you pick (by kind, sender, entity or the decision model) with a circle, asking you first or on its own | built |
| When they're free: a circle can see when you're free (never what), and agents find a time that suits everyone | built |
| Asking someone's agent a question (what does the trip need), answered only from the namespaces they let that circle ask about, with citations | built |
| [Circles with other servers](federation.md) over ActivityPub: people elsewhere by handle (`sam@lens.example`), shares sent to them signed and theirs received, off until an admin turns it on | built |

## Circles

A circle has a name and people with an account on this Lens. Your circles are yours alone: nobody else sees them,
not even the people in them. The usual ones are offered as suggestions (Immediate family, Close family, Friends,
Colleagues); none is made for you.

You can add people you share a namespace with, and people who have you in a circle of theirs, so your son can add you
back once you have added him. Admins can add anyone. Adding someone tells them nothing; sharing does. Once an admin
turns it on, a circle can also hold people on other Lens or ActivityPub servers, by handle
([Circles with other servers](federation.md)).

**In the web app**, your circles are under Profile: a suggested name makes a circle in one click, *Add someone…*
adds a person, the × by a name takes them out, and deleting a circle is undone from its message.

## Sharing

Share one thing at a time with one or more of your circles, or with people in them:

- **An activity** from your [timeline](timeline.md), by its id: an email that came, a calendar event, a sensor alert.
- **A resource** you can read, by its id.

Add a note in your own words ("Sam's trip is on Friday: he needs a packed lunch"). Each person it goes to gets a
`share.received` activity in their own timeline, which they alone see; you get `share.sent` in yours. A share keeps
what it said when you shared it: the name, the line the timeline gave it, the entities it is about, the kind of
activity and your note. Their automations can follow `share.received` like any other kind ("when Dad shares a school
notice, add it to my calendar"), and their agent reads the note and snapshot as outside text.

In the web app, open an item in the [timeline](timeline.md) and pick **Share**, or pick **Share with a circle…** in
a resource's ⋯ menu. Your only circle is already picked; add a note and share. The message that says it was shared
has **Undo**. Shares show in the timeline under *Shared*.

**With the item.** A share says what the item is, not what is in it. Turn on *with the item* to also give each
person who can't open the resource permission on that one resource, as an owner gives permission on an asset
([Access](access.md#permission-on-an-asset)): they then open it on the visitors' pages. It needs the `publish`
permission in its namespace. People who are members of its namespace already open it in the workspace, and their
share links there.

**Undo.** Take a share back and it leaves every timeline it reached, yours too, and the permissions it gave are taken
back. Deleting a circle doesn't take back what was shared with it.

**Vaults.** Nothing in a [vault](encryption.md#vaults) can be shared: what's in a vault stays in it.

## Rules

Rules are [automations](automations.md) with the **Share with a circle** action (`share_with_circle`): pick what to
follow with the automation's triggers and conditions (the school's emails, a calendar's events, a sensor's alerts, an
entity), the circles by name, and a note that can say what came in (`New from school: {{ event.object.name }}`).

Sharing reaches other people, so a rule asks you first: the share waits in [Approvals](automations.md#approvals) until
you say yes. Let its node run on its own (only you can, signed in) and it shares as soon as the activity comes in. It
shares as you, only what you can see in the automation's namespaces, never from a vault, and is audited as by the
automation; its share says `via: automation`.

## When they're free

Turn on *They can see when you're free* on a circle (`lets: ["free_busy"]`), and its people, or their agents, can
ask when you're busy in a window of up to 31 days. They get only when: each event's start and end, never what it is,
where, or with whom. Nothing is shared until you turn it on, and each ask shows in your timeline as `agent.asked`
("Sam's agent asked when you're free, Mon 20 Oct – Fri 24 Oct").

Your events are the calendar events you can read that you organise or are invited to by your account's email, and
every event in a namespace you alone belong to (your own calendar). Nothing in a vault counts. The answer also gives
the free stretches, at least so many minutes long and within the hours of the day asked for, when the person asking
and everyone who let them see are free, to suggest a time for a call or a trip. No model is called.

## Ask their agent

Turn on *They can ask your agent questions* on a circle (`lets: ["questions"]`) and pick the namespaces its answers
come from (`about`, never a vault). Its people, or their agents, can then ask your agent ("what does Sam's trip need?").
It answers only from excerpts found in those namespaces, cites them ([1]), and says when it can't tell. The question is
outside text to the model, so it can't talk your agent into anything; the worst it can do is read what you picked.

You see each question and the answer in your timeline (`agent.asked`). Each answer is one model call in the activity
ledger, counted for both of you; with no model set up, the matching excerpts come back instead. A source links to its
page only when the person asking can open it.

## For agents

Everything here works for agents as it does for people.

- **API:** `GET /api/v1/circles` (your circles, and the suggested names you haven't used), `GET /api/v1/circles/people`
  (who you can add), `POST /api/v1/circles` (`name`, `members` by account id or email), `GET`, `PATCH` (`name`,
  `members`, `add`, `remove`) and `DELETE /api/v1/circles/{id}`. Share with `POST /api/v1/circles/shares`
  (`activity` or `recording`, `circles`, `people`, `note`, `with_item`), list with
  `GET /api/v1/circles/shares?direction=received|sent`, and take one back with `DELETE /api/v1/circles/shares/{id}`.
  Audited as `circle.create`, `circle.update`, `circle.delete`, `circle.share` and `circle.unshare`.
  `POST /api/v1/circles/when-free` (`people`, `start`, `end`, `tz`, `minutes`, `day_start`, `day_end`) answers when
  they're busy and when everyone is free; a circle's `lets` turns it on. `POST /api/v1/circles/ask` (`person`,
  `question`) asks their agent; a circle's `lets: ["questions"]` and `about` (namespaces) let it.
- **MCP:** `list_circles` (with `shares`: received or sent), `save_circle`, `share_with_circle`, `undo_share`,
  `when_free`, `ask_their_agent` and `circles_elsewhere`. They are about the person, so a namespace's own MCP server doesn't offer them.
- **The assistant:** `circles` reads them, `share_with_circle` asks the person first (telling other people is always
  their pick), `when_free` finds a time, and `ask_their_agent` asks someone's agent.
- **Automations:** the `share_with_circle` action ([Rules](#rules)), which agents can add with the automation tools.

## What keeps it safe

- You only add people you already share a namespace with, who have added you, or anyone if you're an admin, so a
  circle doesn't reveal who has an account here.
- A share goes to people in your circles, never to anyone else, and only of what you can see yourself (the timeline's
  rules decide).
- The snapshot is outside text to every agent that reads it (`lens.untrusted`), wrapped as data in prompts like an
  email's subject.
- Someone removed for good loses their circles and what was shared with them; what they shared stays, under their
  name.

## Refine later

- Count every time a repeating event happens, not only its first, for when someone is free.

- Show a received share's sender why they can or can't open it, and let a recipient ask for the item.
- Send a share to the person's chat app (Telegram, Slack, Matterbridge) when they linked one, beside the timeline.
