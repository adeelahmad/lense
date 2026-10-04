# Topics

Each namespace keeps a controlled vocabulary of topics: what its recordings are about. Topics are separate from
entities. Entities are the named things a transcript mentions (people, organisations, products, places); a topic is a
subject someone chose for the vocabulary, like "Gene therapy" or "Funding".

Status: **built**: the vocabulary (labels, definitions, broader, narrower and related topics), recordings about
topics, topics made from topic-like entities, the API, topics in the graph and in RDF. **Planned**: a Topics page,
topic suggestions from analysis, `#` links in notes pointing at topics, and assistant and MCP tools for topics.

## A topic

A topic follows SKOS, the W3C standard for vocabularies:

| Field | SKOS | What it is |
| --- | --- | --- |
| label | `skos:prefLabel` | Its name. Unique in the namespace. |
| alt | `skos:altLabel` | Other labels it goes by (synonyms, abbreviations, spellings). Unique in the namespace too. |
| definition | `skos:definition` | What it covers, in your words. |
| broader | `skos:broader` | The wider topics it belongs under. A topic can have several; it can't end up under itself. |
| narrower | `skos:narrower` | The topics that have it as broader (kept from their side). |
| related | `skos:related` | Topics that are related but not broader or narrower. Kept on both sides. |

Recordings are **about** topics. A person says so, a topic made from an entity brings the recordings that mention
it, and (planned) analysis suggests topics for someone to accept. Each link records where it came from (`person`,
`entity`, `analysis`) and whether it holds (`accepted`) or waits (`suggested`).

## Topics and entities

Until now a topic was an entity of type TERM, which is also the type analysis gives anything it can't classify. Turn
such an entity into a topic (`POST /api/v1/entities/{id}/topic`): its name becomes the label, its aliases the other
labels, its description the definition, and the recordings that mention it are about the topic. The entity is hidden
while the topic exists; deleting the topic shows it again. If the namespace already has a topic of that name, the
recordings join it.

Deleting a topic moves its narrower topics up to its broader ones. Merging topics makes the others' labels the kept
topic's other labels and brings their recordings over.

## Rights

Reading needs a role in the namespace; changing the vocabulary or which recordings are about a topic needs the editor
role there. Every change is in the audit log (`topic.create`, `topic.update`, `topic.delete`, `topic.merge`,
`topic.tag`, `topic.untag`, `topic.from_entity`).

## The API

| Call | What it does |
| --- | --- |
| `GET /api/v1/topics?ns=&q=&top=&broader=` | Topics by label, with how many recordings are about each and how many narrower topics it has. |
| `GET /api/v1/topics/{id}` | A topic with its broader, narrower and related topics and the recordings about it. |
| `POST /api/v1/namespaces/{name}/topics` | Add a topic `{label, alt, definition, broader, related}`. |
| `PATCH /api/v1/topics/{id}` | Change any of those; an empty definition clears it. |
| `DELETE /api/v1/topics/{id}` | Delete it. |
| `POST /api/v1/topics/merge` `{keep, others}` | Merge topics into one. |
| `POST /api/v1/topics/{id}/recordings` `{recordings, remove}` | Say recordings are about it, or aren't. |
| `GET /api/v1/resources/{id}/topics` | The topics a recording is about, and those suggested. |
| `POST /api/v1/entities/{id}/topic` | Make a TERM entity a topic. |

## In the graph and in RDF

In the property graph ([The graph](graph.md)) a topic is a `Topic` node `t<id>`, and recordings link to it with
`ABOUT`; broader topics link to narrower ones with `NARROWER`, and related ones with `RELATED`. ABOUT and NARROWER are
part of the hierarchy, so a topic's ancestors are its broader topics and the recordings about it. TERM entities are
labelled `Term` there, so they aren't confused with topics.

```cypher
// recordings about gene therapy or anything narrower
MATCH (t:Topic)-[:NARROWER*0..6]->(n:Topic)<-[:ABOUT]-(r:Recording)
WHERE toLower(t.name) = 'gene therapy'
RETURN DISTINCT r.name, n.name
```

In RDF ([Linked data](rdf.md)) a topic is `/id/topic/<id>`, a `skos:Concept` in the namespace's vocabulary
`/id/namespace/<name>#topics` (a `skos:ConceptScheme`), and recordings name the topics they are about as
`dcterms:subject`.

## Refine later

- Vocabularies shared by several namespaces, and imported schemes (LCSH, Wikidata) linked with `skos:exactMatch`.
- Entities as authority records: variant names and external identifiers.
- History and undo for topic merges and deletes.
