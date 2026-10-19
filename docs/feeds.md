# Published feeds (RSS and podcasts)

**Status: built.** Feeds, their private addresses, the API, the MCP tools, the workflow nodes that let a routine fill a
feed or make an episode on a schedule, the Feeds page in the web app, and a saved collection's podcast feed, with its
address on the collection's page.

A namespace can publish what's in it as a feed that any feed reader or podcast app follows: new resources, podcast
episodes, things on the timeline, or text a person or an agent wrote. Each feed has a private address; add it to the
app on your phone and new items arrive there.

## Two kinds

| Kind | Lists | For |
|---|---|---|
| `rss` | everything added to it: resources, episodes, timeline activities and text | feed readers |
| `podcast` | items with audio: podcast episodes once their audio is made, and resources that have audio (or video) | podcast apps (Apple Podcasts, Pocket Casts, Overcast, AntennaPod and the rest) |

Both are RSS 2.0. A podcast feed adds Apple Podcasts' tags (author, category, explicit, artwork, each episode's
duration) and each item's audio as its enclosure, which podcast apps download with byte ranges.

## What an item shows

- **A resource**: its title and summary as they are now, a link to its page in the web app, and its audio when it
  has some. A resource deleted since drops out of the feed.
- **A podcast episode** ([Podcasts](podcasts.md)): listed once it's made. Its description is the outline's summary,
  then the sources it cites, each with a link, so a listener can check every claim. In a podcast feed it's listed
  once its audio is made; in an RSS feed a script-only episode is listed as text.
- **A timeline activity** ([Timeline](timeline.md)): its one-line summary, when it happened, its kind as the
  category, and a link to what it's about.
- **Text**: a title, text and a link (http or https), such as a digest a workflow or an agent wrote.

An item is listed once per feed: adding the same resource or activity again does nothing, so a routine that runs
again adds only what's new. A feed lists its newest items (50 by default, up to 500) and keeps its newest 1000.

## Private by default

- A feed's address carries a token (`/api/v1/feeds/public/lfd_...`). The token is all a reader needs, so share the
  address as you would a password. Readers of the feed's namespace see the address in the web app and the API; a new
  address (**rotate**) stops the old one at once.
- What a feed shows is bounded by who can already read its namespace: an item's resource or activity is in that
  namespace, or in one that everyone who reads the feed's namespace can read too, and never in a
  [vault](encryption.md). A vault can't publish a feed, since its resources open only with a passkey.
- A feed asks podcast directories not to list it (`itunes:block`, `podcast:locked`) and search engines not to index
  it (`X-Robots-Tag`), and its address isn't sent on to other sites (`Referrer-Policy: no-referrer`).
- Text in an item is escaped for the XML and again for readers that show a description as HTML, so a title or a
  transcript can't add markup or scripts to a reader.
- Nothing is published until someone makes a feed and adds to it, and a feed changes nothing else in Lens.

Feed readers poll; the feed answers `304 Not Modified` when nothing changed (its `ETag`), and notes when a reader
last read it (`fetched_at`, at most once a minute), so the web app can show that a podcast app is subscribed.

## In the web app

**Feeds** lists the feeds in the namespaces you can read: each one's kind, namespace, how many items it holds and when
an app last read it. Open it with **Feeds** at the top of the Podcasts page, or Jump to; once there's a feed, it's also
under Explore in the navigation (left out before that, so a phone's menu stays as short as it was). **New feed** asks
for a title (focused; Enter makes it), the kind and the namespace, which is the one picked in the top bar when you can
edit it; the feed opens on its page.

A feed's page shows its address with a **Copy** button, when an app last read it, and **New address** (editors), which
stops the old one at once. Editors change what readers show (title, description, author, language, category, artwork,
how many items a reader sees, and whether a podcast is explicit) and save with Enter. The items are listed newest
first, each a link to what it's about; **Remove** takes one out at once with an Undo in the toast, which adds the same
thing again. **Delete** deletes the feed at once with an Undo, which makes it again with its settings and items, at a
new address (the old token is gone for good). What a feed listed stays in Lens either way.

On the canvas, a graph workflow's palette has the publishing nodes under Data (Select assets, Select activities) and
Keep (Publish to feed, Podcast episode); a feed is picked from a list, and the canvas names it on the node.

## A collection's podcast feed

A saved collection ([collections](api.md#collections)) is a podcast of its own: its episodes are the assets in it
with audio or video, newest first (the newest 50), and a filter's new matches arrive as new episodes. On the
collection's page, **Podcast feed** shows your address for it with **Copy**: paste it into your podcast app as a show
added by its address. Nothing has to be made first, and nobody but you has to do anything.

- **One address per person.** Everyone who may read the collection (its owner, and everyone when it's shared) has
  their own address, and it lists only what they can read when their podcast app reads it, so sharing a collection
  never shows anyone else's assets. A vault's assets are never in it.
- **The same private address as a namespace's feed.** It is an `lfd_` token under `/api/v1/feeds/public/`, kept
  sealed and found by its hash, with the same RSS 2.0 and Apple Podcasts' tags, the same audio with byte ranges, and
  the same requests to directories and search engines to keep out. **New address** (rotate) stops the old one at
  once.
- **It stops when it should.** Deleting the collection deletes its feeds. When the collection is no longer shared
  with you, or your account is disabled, your address stops working.
- Its title and description are the collection's, as they are when the app reads it.

| | |
|---|---|
| `GET /api/v1/collections/{id}/feed` | your podcast feed of a collection you may read: its private address (`url`), how many `episodes` it lists now and when a podcast app last read it (`fetched_at`). Made the first time you ask (audited as `feed.create`), the same one after |
| `POST /api/v1/collections/{id}/feed/token` | a new address; the old one stops working. Audited as `feed.token` |
| `DELETE /api/v1/collections/{id}/feed` | stop it: its address stops working (asking again makes a new one). Audited as `feed.delete` |

Agents get the address with the MCP tool `list_feeds` and `collection_id` (on the main MCP server, since a
collection spans namespaces). A collection's feed isn't in `GET /api/v1/feeds`, which lists namespaces' feeds.

## From a routine

A routine fills a feed on a schedule with a graph workflow ([Publishing from the graph](processing.md#publishing-from-the-graph)):
**Select assets** or **Select activities** picks what's new since the routine last ran, in the namespaces it covers,
and **Publish to feed** adds it, or **Podcast episode** makes an episode about it and adds that. Each run adds only
what's new, and what the feed's readers couldn't read is refused.

## API and MCP

| | |
|---|---|
| `GET /api/v1/feeds` | feeds in the namespaces you can read (or `?namespace=`), each with its private address (`url`), how many items it holds and when a reader last read it |
| `POST /api/v1/feeds` | make a feed (editors of its namespace): `namespace`, `title`, `kind` (`rss` or `podcast`), and optionally `description`, `author`, `language`, `category`, `explicit`, `image` (an `https://` address) and `max_items`. Audited as `feed.create` |
| `GET /api/v1/feeds/{id}` | one feed |
| `PATCH /api/v1/feeds/{id}` | change its settings (editors) |
| `DELETE /api/v1/feeds/{id}` | delete it and its items (editors); its address stops working. What the items were about stays |
| `POST /api/v1/feeds/{id}/token` | a new private address (editors); the old one stops working |
| `GET /api/v1/feeds/{id}/items` | its items, newest first |
| `POST /api/v1/feeds/{id}/items` | add `recordings` (resources and episodes), `activities` (timeline activity ids) and `texts` (`{title, summary, link, guid}`) (editors, who can read each one). Answers what was `added`, how many were there `already`, and what was `refused`, with why. Audited as `feed.publish` |
| `DELETE /api/v1/feeds/{id}/items/{item}` | take an item out (editors) |
| `GET /api/v1/feeds/public/{token}` | the feed itself, as feed readers and podcast apps read it: no session, the token is the credential |
| `GET /api/v1/feeds/public/{token}/items/{item}/audio` | an item's audio, with byte ranges (and `HEAD`) |

Agents use the MCP tools `list_feeds` (each feed's address, or one feed with its newest items), `create_feed` and
`publish_to_feed` ([MCP](mcp.md)).

Refine later: a collection feed's settings (artwork, how many episodes, its author) in the web app; undo for a new
address; artwork Lens makes for a feed without an `image`; Atom and JSON Feed alongside RSS; chapters and
transcripts in a podcast feed (`podcast:transcript`); an automation action that publishes to a feed when something
happens; a preview of the feed's XML on its page; Try on the canvas listing what Publish to feed would add, item by
item.
