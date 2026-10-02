# Analytics: how the archive is used

Lens counts what people do with resources, so that the people responsible for a namespace or a collection can see
what's used: how often a resource is opened, played, downloaded and commented on, and how often people search.

## What is kept

Each action is one row: **the account that did it, the resource and its collection, and the time**. Nothing else:
no address, no browser, no search words, no page address.

| Action | Counted when |
|---|---|
| view | someone opens a resource's page in the workspace, or its public page. Once per person, resource and half hour, since pages reload their data |
| play | the player starts playing, once per page load (the web app says so: `POST /resources/{rid}/played`) |
| search | a search is made (its first page): in the workspace, or the public search |
| download | a transcript export, a supplementary file, a document's own file or the PDF made of it is fetched |
| comment | a comment is written |

Someone who isn't signed in has no account: their actions are counted **without one**, as a visitor's. So are files
fetched through a signed link, which doesn't say who follows it. What agents do through the [MCP server](mcp.md) or
an API key is counted as the person's, like the rest.

Actions are kept for `analytics.retention_days` (90 unless an admin changes it; Settings → Analytics). Workers delete
what's older every hour. The **daily counts** (per day, resource and action) hold no accounts, so they stay: charts
keep working past the 90 days, while "how many different people" only covers what's still kept. Deleting a resource
deletes its analytics; moving one takes them to its new namespace.

## Who sees what

| | Sees |
|---|---|
| a namespace's owners | the namespace's numbers: totals, per day, per collection, and its most used resources |
| a collection's admins | the same for their collection and the collections inside it |
| admins | every namespace, and the whole archive at once; how much is kept; purging |
| everyone | their own activity (Account → Your activity): what Lens keeps about them |

Nobody sees who did what: the numbers are counts. Editors and viewers of a namespace don't see its analytics. An app
an admin gave access to ([OAuth](authentication.md#oauth)) doesn't see the whole archive's.

In the web app: **Analytics** in the navigation shows the namespace picked in the top bar (admins: all of them when
none is picked), for 7, 30 or 90 days, with a collection picker for a collection's numbers.

## Purging

Admins: Settings → Analytics shows how many actions are kept and since when, purges what's past its days now, or
deletes everything, the daily counts too (`POST /api/v1/admin/analytics/purge`, audited as `analytics.purge`).

## API

```
GET    /api/v1/analytics?ns=&collection=&days=|from=&to=
GET    /api/v1/analytics/me?limit=&before=
POST   /api/v1/resources/{rid}/played
POST   /api/v1/public/recordings/{rid}/played
GET    /api/v1/admin/analytics
POST   /api/v1/admin/analytics/purge
```

`GET /analytics` answers `{from, to, namespace, collection, totals, people, anonymous, days, collections | namespaces,
resources}`: `totals` and each row are `{view, play, search, download, comment}`; `days` has every day of the range
(UTC), `resources` the 20 most used. With `ns`, owners of the namespace (403 for its other members, 404 without a
role); with `ns` and `collection`, the collection's admins too; without `ns`, admins. A range is at most a year.
Searches of every namespace at once belong to no namespace: only the whole archive's numbers have them.

`GET /analytics/me` lists your own actions, the latest first (`before` pages back): `{at, action, resource, title,
namespace}`, the title only while you can still read the resource.
