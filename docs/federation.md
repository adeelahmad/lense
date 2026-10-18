# Circles with other servers

Your family isn't always on the same Lens. Your partner may run their own, and your parents may use another
ActivityPub server. With circles across servers, you put them in a circle by their handle (`sam@lens.example`), and
what you share with that circle reaches them too, in their own timeline. What they send you lands in yours. It uses
ActivityPub, the open standard Mastodon and others speak, so nothing ties you to Lens.

Status: built. Sharing travels between servers; asking someone's agent and seeing when they're free work on the same
Lens only (see [Refine later](#refine-later)).

## Turning it on

It is off until an admin turns it on, in Settings → Security and access → Circles elsewhere:

| Setting | Default | What it does |
| --- | --- | --- |
| `federation.enabled` | `false` | let circles hold people on other servers, and answer WebFinger and `/ap` |
| `federation.url` | empty | this Lens's public `https://` address; empty means the [HTTPS](https.md) domain's |

Other servers must reach Lens over https:// at that address, so it needs [HTTPS](https.md) on your own domain, or a
[tunnel](remote-access.md) with a fixed hostname set as `federation.url`. Without one, nothing is turned on.

Even then, nobody can be found until they say so. Under Profile → Circles, *People on other servers can find you*
gives you a handle (your name or email's first part, `@` the Lens's domain) and a key pair to sign what you send.
Turning it off deletes both; servers that knew you can no longer reach you.

## People elsewhere in your circles

Under Profile → Circles, type someone's handle under a circle and press Enter. Lens looks them up the way Mastodon
does (WebFinger at their server, then their ActivityPub actor), checks that their actor and key are their own, and
keeps their name, inbox and public key. They show in the circle with a dashed border; the × takes them out.

Adding someone tells them nothing. For them to send you things, they add your handle to a circle of theirs.

## What travels

**What you share.** Sharing with a circle that holds people elsewhere (from the timeline, a resource, a rule or an
agent) sends each of them a `Create` of a `Note`: the share's name, your note and the line the timeline showed. The
item itself, its links into your Lens and anything in a vault never leave. Each is signed with your key
([HTTP Signatures](https://datatracker.ietf.org/doc/html/draft-cavage-http-signatures), `rsa-sha256` over
`(request-target)`, `host`, `date` and `digest`) and sent from a queue: tried again after 1, 5 and 30 minutes, then 2
and 12 hours, then given up with the reason. `GET /api/v1/circles/shares/{id}/elsewhere` says how each is getting on.
Undoing the share sends a `Delete` to everyone it reached.

**What they share with you.** A signed `Create` of a `Note` sent to your inbox shows in your timeline as
`share.received` from them, once, and their `Delete` (or `Undo`) takes it back. Lens takes it only when:

- the signature checks out against the key Lens kept when you added them (Lens never fetches a key an inbox request
  points to), over the request target, host, date and a digest that matches the body;
- its date is within five minutes;
- the activity's actor is who signed it; and
- they are in one of your circles.

Its text is taken out of any HTML and marked as outside text (`lens.untrusted`), so agents read it as data, never as
instructions. Everything else sent to an inbox is accepted and dropped.

Nothing is public: your outbox is always empty, your actor says it isn't discoverable or indexable, and no model is
called.

## For agents

- **API:** `GET` and `PUT /api/v1/circles/elsewhere` (`findable`) read and set whether you're findable, with your
  handle. `POST /api/v1/circles` takes `remote` (handles), `PATCH /api/v1/circles/{id}` takes `add_remote` and
  `remove_remote`, and a share answers `elsewhere`: who it's being sent to. Audited as `circle.findable`.
- **MCP:** `circles_elsewhere` (read, or set `findable`) and `save_circle`'s `add_remote` and `remove_remote`.
- **ActivityPub:** `GET /.well-known/webfinger?resource=acct:<handle>`, `GET /ap/users/<name>` (the actor, with its
  public key), `GET /ap/users/<name>/outbox` (empty) and `POST /ap/users/<name>/inbox`. They answer 404 while it is
  off, and for anyone who isn't findable.

## What keeps it safe

- Off by default, and each person decides whether they can be found. Nobody is listed or discoverable.
- Lens fetches only `https://` addresses at public IP addresses (and the private networks an admin allowed in Settings
  → Documents), through the same guard web pages are captured with; an inbox request never makes Lens fetch anything.
- Private keys are sealed with the server's secret; removing an account deletes its keys, queue and what it received.
- See `threat-model.md` (federation entry point, P-FEDERATION).

## Refine later

- Asking someone's agent, and when they're free, between servers (`Question` activities).
- A person's key rotated on their server: fetch their actor again when a signature fails.
- Deliver a share to everyone on one server through its shared inbox.
- Shares from Mastodon and other servers that send `Announce` or a direct `Note` without `Create`.
