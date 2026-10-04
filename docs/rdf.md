# Linked data (RDF)

Everything in the archive is also linked data. Each recording, collection, namespace, entity and speaker has a URI,
and that URI returns its description in RDF. The description uses Dublin Core
([DCMI Metadata Terms](https://www.dublincore.org/specifications/dublin-core/dcmi-terms/)), SKOS, FOAF and OWL, plus
a small Lens vocabulary for what those don't cover.

The RDF is generated from what Lens already stores: the recording's metadata (see [IIIF](iiif.md)), its entities and
its speakers. It can't go out of date, and nothing needs migrating.

## URIs

URIs use the address Lens publishes (`iiif.base_url`, or the address a request arrived at):

| URI | What it is |
| --- | --- |
| `/id/recording/<id>` | a recording, typed `lens:Item` and a DCMI type (`Sound`, `MovingImage`, `Text`, `StillImage`) |
| `/id/collection/<id>` | a collection (`dcmitype:Collection`), `dcterms:isPartOf` its parent or its namespace |
| `/id/namespace/<name>` | a namespace: a `dcmitype:Collection` and the `skos:ConceptScheme` of its entities |
| `/id/entity/<id>` | a `skos:Concept`, also `foaf:Person`, `foaf:Organization`, `dcterms:Location` or `dcmitype:Event` depending on its type |
| `/id/topic/<id>` | a topic: a `skos:Concept` in `/id/namespace/<name>#topics` (the namespace's `skos:ConceptScheme` of topics), with `skos:broader`, `skos:narrower` and `skos:related`; recordings about it name it as `dcterms:subject` ([Topics](topics.md)) |
| `/id/speaker/<id>` | a `foaf:Person`, `owl:sameAs` the speakers declared to be the same person |
| `/id/field/<id>` | a custom field, used as the property its values are given with |
| `/ns` | the Lens vocabulary (`lens:`), in Turtle |

Each URI uses content negotiation. An `Accept` of `text/turtle`, `application/ld+json`, `application/n-triples` or
`application/rdf+xml`, or `?format=turtle|json-ld|nt|xml`, returns RDF. A browser is sent to the page instead
(303 See Other).

A public recording's URI is open to everyone, just like its IIIF manifest. Visitors get its metadata and published
custom fields. Members also get the entities it mentions (`dcterms:references`), its access and its internal fields.
Collection, namespace, entity and speaker URIs are for the namespace's members only. Anyone else gets a 404, as if
they weren't there.

## Mapping

| Lens metadata | RDF |
| --- | --- |
| label, summary | `dcterms:title`, `dcterms:description` (language-tagged) |
| creators, contributors | `dcterms:creator`, `dcterms:contributor`: the speaker, the person's URI, or a node with `foaf:name` |
| subjects | `dcterms:subject`: the entity, the subject's URI, or its label |
| navDate | `dcterms:created` (`xsd:dateTime`) |
| language | `dcterms:language` (`dcterms:RFC5646`) |
| rights, attribution | `dcterms:license`, `dcterms:rights` |
| provider | `dcterms:publisher`, a `foaf:Organization` |
| identifiers | `dcterms:identifier` (typed ones also as `lens:identifier`) |
| homepage, related | `foaf:homepage`, `dcterms:relation` |
| label/value pairs | `lens:property` (`rdfs:label`, `rdf:value`) |
| terms | the DCMI term itself (`dcterms:spatial`, `dcterms:alternative`, …) |
| duration, file type | `dcterms:extent` (`xsd:duration`), `dcterms:format` (`dcterms:IMT`) |
| where it is | `dcterms:isPartOf` its collection or namespace |

`terms` holds every other DCMI term a recording can have: alternative, abstract, audience, coverage, spatial,
temporal, source, issued, modified, rightsHolder, provenance, isVersionOf, replaces, requires and the rest. Each
term holds a list of values. A value that is an http(s) address becomes a link. The oai_dc record (`/iiif/<id>/dc.xml`)
includes those of them that refine one of its 15 elements (spatial becomes `dc:coverage`, alternative becomes
`dc:title`, and so on).

## API

- `GET /api/v1/recordings/{id}/rdf` returns a recording with the entities it mentions.
- `GET /api/v1/namespaces/{name}/rdf` returns a whole namespace: its collections, recordings, entities and speakers.
  Add `download=true` to save it as a file.

Both accept `?format=` or an `Accept` header, and return Turtle when neither is given.

## SPARQL

`GET /api/v1/namespaces/{name}/sparql?query=…` (or `POST` with `{query}`) runs a read-only SPARQL query over the
namespace's graph, the same graph `/rdf` returns. It is for the namespace's members. SELECT and ASK return SPARQL 1.1
JSON results (`application/sparql-results+json`). CONSTRUCT and DESCRIBE return RDF (Turtle unless `format` or
`Accept` asks for another format). The prefixes dcterms, dcmitype, foaf, skos, owl, rdf, rdfs, xsd and lens are
already known. `SERVICE` and `FROM` are refused, because a query never reaches outside the archive. A SELECT returns
at most 10,000 rows. The same query is available to agents as the MCP tool `sparql`. The app runs it from a
Collection page's SPARQL button.

```sparql
# entities mentioned in the most recordings
SELECT ?name (COUNT(?r) AS ?n) WHERE { ?r dcterms:references ?e . ?e skos:prefLabel ?name }
GROUP BY ?name ORDER BY DESC(?n) LIMIT 20
```

## Import

`POST /api/v1/namespaces/{name}/rdf/import` reads Dublin Core descriptions into the namespace's recordings. It is for
editors, and the body is `{data, format, dry_run}`.

- Turtle, N-Triples and JSON-LD are accepted. A JSON-LD document must have its `@context` inline, because Lens
  doesn't fetch anything while reading. RDF/XML isn't accepted. The limit is 5 MB.
- A description is matched to a recording by its URI (`/id/recording/<id>`). Failing that, it is matched by an
  identifier the recording already has (`dcterms:identifier`, `dc:identifier` or `lens:lensId`). Descriptions that
  match nothing are listed but not created.
- DCMI terms and the 15 DC 1.1 elements fill the fields in the mapping above. Speakers and entities named by URI must
  belong to the namespace. A license that isn't Creative Commons or RightsStatements.org is kept as a statement.
- Any other statement about the recording is kept as it came (`statements`) and included again on export. Exporting a
  recording and importing it back changes nothing.
- Terms and statements add to what the recording already has. Other fields replace what it has.
- `dry_run` (the default) only reports what would change. Otherwise every change is a metadata edit that is kept in
  the recording's history and can be reverted, and the import is written to the audit log.

## Refine later

- A permanent vocabulary URI shared by every Lens. Today `lens:` is `<address>/ns#`, so two archives use different
  terms.
- Roles qualified per recording. Today a speaker's role is left out, because the speaker is the same resource in
  every recording.
- Collections and entities that visitors can see in public namespaces.
- Importing descriptions that match no recording as new resources, and importing collections, entities and speakers.
- SPARQL builds the namespace's graph for every query. Cache it, or keep a triple store in step with the database,
  once namespaces get large. There is also no query timeout yet.
