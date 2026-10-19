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
| Rules per circle: what your agent shares without asking (kinds, entities, a match tree) and what it asks you first about | planned |
| Asking someone's agent (when are they free, what does the trip need), answered only from what they let that circle see, with citations | planned |
| Circles across Lens servers over ActivityPub: a signed inbox, an outbox and WebFinger handles like `sam@lens.example` | planned |

## Circles

A circle has a name and people with an account on this Lens. Your circles are yours alone: nobody else sees them,
not even the people in them. The usual ones are offered as suggestions (Immediate family, Close family, Friends,
Colleagues); none is made for you.

You can add people you share a namespace with, and people who have you in a circle of theirs, so your son can add you
back once you have added him. Admins can add anyone. Adding someone tells them nothing; sharing does.

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

## For agents

Everything here works for agents as it does for people.

- **API:** `GET /api/v1/circles` (your circles, and the suggested names you haven't used), `GET /api/v1/circles/people`
  (who you can add), `POST /api/v1/circles` (`name`, `members` by account id or email), `GET`, `PATCH` (`name`,
  `members`, `add`, `remove`) and `DELETE /api/v1/circles/{id}`. Share with `POST /api/v1/circles/shares`
  (`activity` or `recording`, `circles`, `people`, `note`, `with_item`), list with
  `GET /api/v1/circles/shares?direction=received|sent`, and take one back with `DELETE /api/v1/circles/shares/{id}`.
  Audited as `circle.create`, `circle.update`, `circle.delete`, `circle.share` and `circle.unshare`.
- **MCP:** `list_circles` (with `shares`: received or sent), `save_circle`, `share_with_circle` and `undo_share`. They
  are about the person, so a namespace's own MCP server doesn't offer them.
- **The assistant:** `circles` reads them, and `share_with_circle` asks the person first: telling other people is
  always their pick.

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

- Show a received share's sender why they can or can't open it, and let a recipient ask for the item.
- Send a share to the person's chat app (Telegram, Slack, Matterbridge) when they linked one, beside the timeline.
