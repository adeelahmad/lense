# The graph

Lens keeps the archive as a property graph that people explore on a canvas and agents query with Cypher. It is built
live from the database (`app/domain/graph_model.py`), so it is never out of step and costs no extra storage.

Status: **built**: the graph model, walking it (parents, children, ancestors, descendants, neighbours, paths),
read-only Cypher and asking for changes over the API; the explorer canvas (drag, one-click layouts, a node menu,
touch); questions in plain language; graph tools for the assistant and MCP. **Planned**: topics as a controlled
vocabulary (SKOS), apart from entities.

## The explorer

Graph in the sidebar shows the graph of the scope picked at the top (one namespace, or all shared).

- **Move around**: drag the background to pan, the wheel or the + and − buttons to zoom; on touch, drag with one
  finger and pinch with two. Drag a node to put it where you want; it stays there on this device until a layout or
  Reset.
- **Layouts**: Force (the default), BFS tree, DFS tree and Radial, one click each, from the busiest node or from a
  node you pick. Reset puts everything back as it was.
- **Node menu**: right-click a node (long-press on touch, or select it and press M) for its parents, children, all
  ancestors, all descendants and neighbours, paths to another node (pick it next), a BFS, DFS or radial layout from
  it, adding it to a route, its details and its page, or hiding it. What is found joins the canvas and lights up.
- **Route**: add stops from the node menu; the route follows the shortest path between each pair of stops, laid out
  left to right.
- **Ask**: the bar at the bottom takes a question in plain words or Cypher (anything starting with `MATCH`). The
  answer shows the Cypher used, which you can edit and run again (Ctrl or Cmd+Enter), and the rows; the nodes it
  found join the canvas and light up, and a node in the rows selects it. Questions need a language model (Settings);
  Cypher works without one.

## What is in it

| Node | Id | Properties |
| --- | --- | --- |
| Namespace | `n<id>` | name, shared |
| Collection | `c<id>` | name, namespace |
| Recording | `r<id>` | name (the title), date, media, namespace |
| Speaker | `s<id>` | name, seconds (talk time), namespace |
| Entity | `e<id>`, or `e:<key>` in the global scope | name, type, key, mentions, namespaces, ids |

An entity has its type as a second label: `Person`, `Organisation`, `Product`, `Place`, `Event`, `Work`, `Topic`, or
a namespace's own type (`MY_TYPE` becomes `MyType`). Every node also has `id`, `name` and `namespace`. Hidden entities
and quiet types (dates and numbers) are left out, as in the overview graph.

| Relationship | From → to | Properties |
| --- | --- | --- |
| CONTAINS | Namespace → Collection → Collection → Recording | |
| HAS_SPEAKER | Recording → Speaker | seconds |
| MENTIONS | Recording → Entity | count |
| SAID | Speaker → Entity | count |
| MENTIONED_WITH | Entity → Entity (said in the same line) | count |
| SPOKE_WITH | Speaker → Speaker (in the same recording) | recordings |
| SAME_AS | Speaker → Speaker (said to be one person) | |
| SAME_THING | Entity → Entity (linked across namespaces) | |

CONTAINS, HAS_SPEAKER, MENTIONS and SAID are the hierarchy: an entity's parents are the recordings that mention it and
the speakers who said it; its ancestors go on up to collections and the namespace.

## Scope and rights

Every call names a scope: `ns:<name>` (one namespace, isolated ones too) or `global` (every namespace whose graph is
shared). Either way the graph holds only the namespaces the caller can read, so a query can't reach anything else. In
the global scope, entities with the same name in different namespaces are one node, `e:<key>`, which is what joins
namespaces; `ids` lists the entities behind it, and `e<id>` finds the merged node too.

- **Read**: anyone signed in, any API token or app token (read or write scope), for the namespaces they can read.
- **Change**: a write-scope token or a signed-in person with editor access to the namespaces involved. Changes are
  proposed by default and wait in Proposed changes for someone to accept; `apply` makes one at once. Either way it is
  recorded in the audit log and can be undone.

## The API

| Call | What it does |
| --- | --- |
| `GET /api/v1/graph/schema?scope=` | Labels, relationship types (and what they join), properties, counts and example queries. |
| `GET /api/v1/graph/related?node=&relation=&depth=&types=&limit=&scope=` | `children`, `parents`, `ancestors`, `descendants` (along the hierarchy) or `neighbours` (any relationship), nearest first, with the relationships between them. |
| `GET /api/v1/graph/paths?a=&b=&max_depth=&limit=&types=&directed=&shortest=&scope=` | Paths from a to b, shortest first: every simple path up to `max_depth` hops, or only the shortest ones. |
| `POST /api/v1/graph/query` `{query, params, scope, limit}` | Read-only Cypher: `{columns, rows, nodes, edges, truncated, steps, ms}`. Nodes and relationships a query returns are also in `nodes` and `edges`, ready to draw. |
| `POST /api/v1/graph/ask` `{question, scope, limit}` | A question in plain language: the language model writes the Cypher (from the schema and the names the question mentions), Lens runs it, and returns `{question, cypher, explanation, attempts, result}`. A query that fails goes back to the model once with the error. 409 when no model is set up. |
| `POST /api/v1/graph/changes` `{kind, a, b, reason, apply}` | Ask for a change: `merge` two entities of one namespace (a is kept) or `link` entities of two namespaces. |

## The assistant and MCP

The assistant in Chat has the graph as tools, over the namespaces of the chat (or every shared one): `graph_schema`,
`graph_query` (Cypher), `graph_related` and `graph_paths`. They only read.

The MCP server (docs/mcp.md) has the same four, marked read-only, and `propose_graph_change`, which needs a
write-scope token and editor access; the change waits in Proposed changes unless `apply` makes it at once, and can be
undone either way.

## Why Cypher

Agents query the graph in Cypher, the language of Neo4j and the basis of ISO GQL, the standard graph query language.

- Models write it more reliably than the alternatives, and the pattern syntax reads like the graph:
  `(s:Speaker)-[:SAID]->(e:Entity)`.
- It is an open standard (GQL), so queries aren't tied to Lens or to SurrealDB.
- It is declarative, so Lens parses it and runs it itself over the caller's projection; nothing in it can reach
  another table or change data.

Gremlin was passed over because it is step by step, models get it wrong more often, and it needs a Java server that is
too heavy for a Raspberry Pi. Raw SurrealQL would tie agents to the database, can reach any table and is rarely known
by models. SPARQL stays available over the RDF view ([rdf.md](rdf.md)).

Lens runs its own engine (`app/domain/cypher.py`). The Python Cypher library tried first (grand-cypher) failed on
things models write all the time: single-quoted strings, lowercase `count()` and undirected relationships.

### What it understands

- `MATCH` and `OPTIONAL MATCH` with comma-separated patterns: `(n:Label:Other {prop: value})`, `-[r:TYPE|OTHER]->`,
  `<-[]-`, `-[]-` (either direction), variable length `-[*]-`, `-[*2]-`, `-[*1..3]-` (up to 8 hops), named paths
  `p = (...)`, `shortestPath(...)` and `allShortestPaths(...)`. A relationship is used once per match.
- `WHERE` with `AND`, `OR`, `XOR`, `NOT`, `=`, `<>`, `<`, `>`, `<=`, `>=`, `=~` (regular expression), `IN`,
  `STARTS WITH`, `ENDS WITH`, `CONTAINS`, `IS [NOT] NULL`, arithmetic, lists, maps, `$parameters`, list comprehensions
  `[x IN list WHERE ... | ...]` and `CASE`.
- `WITH` and `RETURN` with `AS`, `DISTINCT`, `*`, aggregation (`count`, `sum`, `avg`, `min`, `max`, `collect`,
  `count(*)`, `DISTINCT` inside), `ORDER BY ... ASC|DESC`, `SKIP`, `LIMIT`; `UNWIND`; `UNION [ALL]`.
- Functions: `id`, `labels`, `type`, `properties`, `keys`, `startNode`, `endNode`, `nodes`, `relationships`, `length`,
  `size`, `head`, `last`, `tail`, `reverse`, `coalesce`, `toLower`, `toUpper`, `trim`, `toString`, `toInteger`,
  `toFloat`, `split`, `substring`, `replace`, `left`, `right`, `abs`, `round`, `floor`, `ceil`, `sign`, `sqrt`,
  `range`, `exists`.

`CREATE`, `MERGE`, `SET`, `DELETE`, `REMOVE` and `CALL` are refused with a message pointing at
`POST /api/v1/graph/changes`. A query runs within a budget (3 million steps, 8 seconds) and returns at most `limit`
rows; parts of `WHERE` are checked as soon as their variables are bound, so a starting condition keeps a wide pattern
cheap. Errors say what to fix and where, so an agent can correct its query.

### Examples

```cypher
// who is mentioned most
MATCH (e:Person) RETURN e.name, e.mentions ORDER BY e.mentions DESC LIMIT 10

// which recordings mention Acme, and how often
MATCH (r:Recording)-[m:MENTIONS]->(e:Entity) WHERE toLower(e.name) = 'acme'
RETURN r.name, r.date, m.count ORDER BY r.date

// who talks about Acme
MATCH (s:Speaker)-[x:SAID]->(:Entity {name: 'Acme'}) RETURN s.name, x.count ORDER BY x.count DESC

// how Alice is connected to Acme
MATCH p = shortestPath((s:Speaker {name: 'Alice'})-[*..6]-(e:Entity {name: 'Acme'}))
RETURN [n IN nodes(p) | n.name] AS chain

// everything in a collection, however deep
MATCH (c:Collection {name: 'Interviews'})-[:CONTAINS*1..8]->(r:Recording) RETURN r.name, r.date
```

## Refine later

- Topics as SKOS concepts in schemes (broader, narrower, related), apart from entities, which become authority records
  with preferred and variant names and external identifiers.
- More change kinds for agents (rename, retype, hide, unlink), and Cypher write clauses mapped to proposed changes.
- Collections people were given a role on (without a namespace role) aren't in the graph yet.
- `EXISTS { }` subqueries and pattern predicates in `WHERE`.
- Very large archives: build the projection incrementally instead of per change.
