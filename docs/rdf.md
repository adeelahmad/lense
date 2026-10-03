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

## Refine later

- A permanent vocabulary URI shared by every Lens. Today `lens:` is `<address>/ns#`, so two archives use different
  terms.
- Roles qualified per recording. Today a speaker's role is left out, because the speaker is the same resource in
  every recording.
- Collections and entities that visitors can see in public namespaces.
