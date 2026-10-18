# Published feeds (RSS and podcasts)

**Status: in progress.** Feeds, their private addresses, the API and the MCP tools are built. Routine workflow nodes
that pick part of the graph or the timeline and publish it, and the Feeds page in the web app, come next.

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

Refine later: artwork Lens makes for a feed without an `image`; Atom and JSON Feed alongside RSS; chapters and
transcripts in a podcast feed (`podcast:transcript`); an automation action that publishes to a feed when something
happens.
