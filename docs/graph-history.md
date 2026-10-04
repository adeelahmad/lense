# Graph history

Every change to the entity graph is kept, in order, as an event that is never changed or deleted. Its number is the
graph's version. So the graph can be seen as it was at any version, two versions compared, and one entity's history
read: who renamed it, who merged what into it, which routine linked it, and why.

Built: the event log, versions, as-of, diffs, entity history, named versions, rollback to a version, checkpoints with
replay and verify, the History tab, the graph API and Cypher as of a version. Planned: an as-of picker in the
explorer (DEVLOG.md).

In the web app, Routines › History lists every version; open one to see what changed, name it, or roll back to it
(with a preview). An entity's History button shows only its changes.

## What is versioned

What people and agents curate:

- **Entities**: name, type, description, hidden (and why), on the fixed list, Unknown and Unlabeled.
- **Other names** (`entity_alias`): the ways a name is said that point at an entity, from merges, renames, the
  entity page and names the model placed.
- **Links**: the same thing in two namespaces.
- **Distinct pairs**: two entities someone said are not one, so they aren't suggested again.

Mentions are not versioned: analysis makes them from transcripts, and transcript edits have their own history. An
as-of view uses today's mentions. A moved or removed mention is still an event (`mention.move`, `mention.remove`), so
it shows in both entities' histories.

## An event

| Field | What it holds |
| --- | --- |
| `version` | The graph's version after this change. |
| `at` | When. |
| `op` | What happened: `entity.rename`, `entity.describe`, `entity.retype`, `entity.hide`, `entity.show`, `entity.define`, `entity.undefine`, `entity.aliases`, `entity.delete`, `entity.merge`, `entity.unmerge`, `link.add`, `link.remove`, `distinct.add`, `mention.move`, `mention.remove`, `merge.apply`, `link.apply`, `merge.accept`, `link.accept`, `merge.dismiss`, `merge.undo`, `link.undo`, `analysis`. |
| `actor` | Who: the person's email, `routine:<id>`, `analysis`, or `system`. |
| `via` | Through what: `web`, `token` (an API token), `oauth` (an app), `assistant`, `mcp`, `routine`, `workflow`, `analysis`, `cli`, `system`. |
| `why` | The reason given: a graph change's reason, the model's verdict, why an entity was hidden. |
| `origin` | What it came from: `graph_change`, `merge` (the entity_merge to undo), `routine`, `run`, `workflow`, `recording`, `approval` (an assistant approval), `tool` (an MCP tool), `mention`. |
| `spaces`, `entities` | The namespaces and entities it touched. |
| `ops` | Every record it changed: `{t: table, k: key, b: the row before, a: the row after, s: namespaces}`; no `b` means it was made, no `a` that it went. |

One action is one event: a merge (the other entity going, its names becoming other names of the one kept, its links
moving) is one `entity.merge`, and a routine's merge is one `merge.apply` that points at its graph change.

## The API

All calls see only the namespaces the caller can read (`namespace` narrows it to one). An event that touched a
namespace the caller can't read (a link across namespaces) isn't listed for them. A version is a number, a version's
name, or `head`.

| Call | What it does |
| --- | --- |
| `GET /api/v1/graph/history?namespace=&entity=&before=&limit=` | Versions, newest first, with what changed, who, through what, why and the entities' names; `entity` gives one entity's history; `next` pages back. |
| `GET /api/v1/graph/history/{version}` | One version in full, every record before and after. |
| `GET /api/v1/graph/as-of/{version}?namespace=` | The graph at a version: entities with their other names, links and distinct pairs. |
| `GET /api/v1/graph/diff?from=&to=&namespace=` | What changed between two versions: entities added, removed and changed field by field; other names, links and distinct pairs added and removed. |
| `GET /api/v1/graph/tags` | Named versions. |
| `POST /api/v1/graph/tags` `{name, version, note}` | Name a version (default: the current one), such as "before the cleanup". Editors and admins. |
| `DELETE /api/v1/graph/tags/{name}` | Drop a name; the version stays. |
| `POST /api/v1/graph/rollback` `{to, namespace, dry_run}` | Take the graph back to a version. A preview by default: the changes it would undo, newest first, and the entities, names, links and pairs that would change. `dry_run: false` does it. |

## Rolling back

A rollback undoes every change made since a version, in one namespace or in all the namespaces you can edit, newest
first, and records the whole of it as one new version (`graph.rollback`, with `rollback_to`). It is a change like
any other: rolling back to the version just before it brings everything back, merges included.

- Merges come undone with their mentions; a merge undone since is merged again. Graph changes it takes back are
  marked undone. Moved mentions go back to the entity they were on, and removed ones are said again.
- Then every record is set to how it was at that version.
- What analysis found since stays: it comes from the transcripts, and mentions point at it. Changes people made to
  those entities are still taken back.
- An entity made since that is mentioned now stays (hide it instead); the reply lists it under `skipped`.
- It needs editor access to every namespace the changes touched: a link to a namespace you can't edit stops it.

## Replay and verify

A checkpoint keeps the whole graph as it was at one version. The first is taken with the first recorded change (the
graph as it was before it, so an archive that had entities before this feature replays too), then one every 1000
changes. Replaying starts from the newest checkpoint and applies the events after it, without reading today's rows.

Verify replays the history and compares it with the graph. Anything that differs was written without being recorded
(by an older version of Lens, or by hand in the database). Fixing records it as one `graph.drift` change, so the
history matches again.

| Call | What it does |
| --- | --- |
| `GET /api/v1/graph/verify` | `{same, differences, from, head}` and what differs. Admins. |
| `POST /api/v1/graph/verify` | Record what differs as one change. Admins. |
| `GET /api/v1/graph/checkpoints`, `POST /api/v1/graph/checkpoints` | List checkpoints; keep one now. Admins. |

On the command line: `lens history verify [--fix]`, `lens history checkpoint`, `lens history list`.

## How it works

Code that changes the graph wraps its writes in `graph_history.change(db, op, entities=...)`. It reads the rows of
those entities (and the other names, links and distinct pairs that name them) before and after, and records what
differs as one event; a change made inside another joins it. Who and why come from `graph_history.acting(...)`: each
API request sets the caller and whether they came through the web app, a token or an app; the assistant, MCP tools,
routines, workflows and analysis set their own.

The graph as of a version is today's rows with every later event walked back, newest first. A diff composes the
events between two versions: for each record, how it was before the first and after the last.

The graph API (`/graph/schema`, `/graph/related`, `/graph/paths`) and Cypher (`POST /graph/query`) take `as_of`, a
version or a version's name: entities and their cross-namespace links are as they were then; mentions, recordings and
speakers are today's, so an entity merged away since shows only if it is still mentioned. The explorer's graph is
cached per graph version, so any recorded change shows at once.

## Refine later

- Speakers (same-person links, names) in the history.
- A mention-level history.
- As-of from the nearest checkpoint, so very old versions don't walk back through every later event.
