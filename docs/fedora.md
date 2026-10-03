# Fedora repository

Lens can keep a copy of the archive in [Fedora](https://wiki.lyrasis.org/display/FF) 6, the repository many libraries
and archives use for preservation. Every namespace, collection, recording (with its file), entity and speaker becomes
an LDP resource in Fedora, described in RDF with Dublin Core ([Linked data](rdf.md)). SurrealDB is still the working
database, and Fedora holds a copy that is kept up to date with it.

The copy is off until you give Lens Fedora's address, so installs without Fedora run exactly as before. Fedora is a
Java server that needs about 1 GB of memory, so leave it off on a Raspberry Pi.

## Running it

```sh
docker compose --profile fedora up -d     # Fedora 6.5 at http://127.0.0.1:8081/fcrepo (user fedoraAdmin)
```

Then do one of these:

- Set the address in **Settings → Fedora repository**: `http://fedora:8080/fcrepo/rest`, with the user and password.
- Put `LENS_FEDORA_URL`, `LENS_FEDORA_USER` and `LENS_FEDORA_PASSWORD` in `.env`. These override the app's
  settings, which then show as locked.

The Fedora image's default account is `fedoraAdmin` / `fedoraAdmin`. Change it (Tomcat's `tomcat-users.xml`) before
the server is reachable from anywhere but this machine. You can also point Lens at an existing Fedora 6 instead.

## What goes where

Under `<url>/<root>` (the root is `lens` by default):

| Path | What it is |
| --- | --- |
| `<namespace>` | the namespace (`dcmitype:Collection`) |
| `<namespace>/collections/<id>` | a collection |
| `<namespace>/recordings/<id>` | a recording: dcterms title, creator, subject, created, license and the rest |
| `<namespace>/recordings/<id>/file` | its file (a binary), unless files are off or it is larger than the limit |
| `<namespace>/entities/<id>` | an entity (`skos:Concept`) |
| `<namespace>/speakers/<id>` | a speaker (`foaf:Person`) |

Each resource gets the triples Lens has about it. Blank nodes become hash URIs (`…/recordings/12#b1`), and each
resource also gets `owl:sameAs` pointing to its Lens URI. Links to other resources use their Lens URIs
(`/id/entity/7`), which don't change wherever Fedora is.

## Keeping it up to date

- A change to a recording's metadata is queued and sent at the next sync, every `sync_seconds` (60).
- Every `full_hours` (24), everything is compared with what was last sent, using a hash for each resource. This
  catches what analysis changed, new recordings, and deletions: a resource Lens no longer has is deleted from Fedora
  along with its tombstone.
- Only what changed is sent. A file is sent again only when its size or modification time changes.
- **Compare and send now** in Settings, or `POST /api/v1/admin/fedora/sync`, runs a full comparison straight away.
  `GET /api/v1/admin/fedora` shows the state: resources kept, changes waiting, the last sync and the last problem.

The workers send the changes, and so does the API when it runs its own background work. A problem such as an
unreachable server or a refused resource is shown in Settings and tried again at the next sync. The rest still
goes.

## Testing

`tests/api/test_fedora.py` runs against a stand-in. To run it against a real Fedora:

```sh
docker run -d -p 8080:8080 fcrepo/fcrepo:6.5.1-tomcat9
LENS_TEST_FEDORA_URL=http://127.0.0.1:8080/fcrepo/rest uv run pytest tests/api/test_fedora.py
```

## Refine later

- One process sending the changes. Today the API and the worker may both send them, which is safe because the PUTs
  are idempotent.
- Fedora transactions, so a sync lands as a whole.
- Files Fedora reads in place (external content), instead of a copy.
- Reading Fedora back (restoring an archive from it), and checks on its OCFL storage.
