# IIIF and descriptive metadata

Every recording is published as a IIIF Presentation 3.0 Manifest, and every namespace as a Collection. Any IIIF viewer
can open them, and harvesters can follow them.

- **Manifest** (`/iiif/<id>/manifest`): the audio on a Canvas with its duration.
  - The transcript comes as WebVTT captions plus per-line annotations; speakers and entities come as tagging annotations.
  - Chapters become the table of contents (Ranges).
  - Downloads (`rendering`): vtt, srt, txt, md and json.
  - Supplementary files ([API](api.md#files)): every one is a download (`rendering`, at `/iiif/<id>/files/<file>`).
    Transcripts, captions and translations that say when their lines are also come as WebVTT captions
    (`/iiif/<id>/files/<file>.vtt`, whatever their format), an index as a table of contents (a Range), and a thumbnail
    as the Manifest's thumbnail. Files that need permission sit behind the Authorization Flow; attachments always do.
  - A schema.org record and a Dublin Core record, linked with `seeAlso`.
- **Collections:** `/iiif/collection` lists the namespaces, `/iiif/collection/<namespace>` the namespace's top
  collections, and `/iiif/collection/<namespace>/<id>` one collection: the collections inside it, then its recordings'
  Manifests, oldest first. Each is a Collection with its parent as `partOf`, and a Manifest is `partOf` the collection
  it lives in. Collections show only what the requester may see: one with nothing visible inside it is left out.
- **Content Search 2.0:** `/iiif/<id>/search` and `/autocomplete`, plus `/iiif/collection/<namespace>/search`. Hits come
  with highlighting (TextQuoteSelector).
- **Content State 1.0:** `GET /api/v1/resources/<id>/content-state?t0=&t1=` gives a link to an exact moment, encoded the
  way the spec requires, that compatible viewers open.
- **Change Discovery 1.0:** `/iiif/discovery/activity`, a feed of Create, Update and Delete events for published
  recordings.
- **Authorization Flow 2.0:** protected audio and transcripts carry a probe service.
  - Other viewers open `/iiif/auth/access` to sign in and get a token from `/iiif/auth/token`, which posts it only to
    the viewer's origin.
  - A successful probe returns a short-lived signed link, so playback doesn't depend on third-party cookies.
  - `/iiif/auth/logout` revokes the tokens.
- **Import:** `POST /api/v1/import/iiif` (admins) takes a Presentation 3 Manifest or Collection from another server.
  - A Collection's Manifests are found in the Collections inside it too (at most 8 deep and 50 Collections read).
  - It copies the audio, keeps WebVTT captions as the transcript (speakers included), and maps the metadata.
  - The recordings go into the collection of the namespace you choose (its default unless you pick one).
  - It then queues the namespace's pipeline, skipping transcription.

A recording's access decides what is published ([Access](access.md)). It's set per recording, with a default per
namespace, and only owners change it:

- `public`: published. Its open parts (media, transcript, index) are plain links; closed ones sit behind the
  Authorization Flow. Chapters are ranges when the index is open.
- `restricted` and `private` (the default): not published. The manifest answers 404 unless the request carries
  permission, and collections leave the recording out.

Set `iiif.base_url` to the stable public HTTPS address, since identifiers are built from it. Put the server behind
HTTPS before publishing: the authorization flow requires it, and its cookie is `SameSite=None; Secure`. Other IIIF
viewers also need the public host in `server.allowed_hosts`. `iiif.viewers` holds "Open in" links, using `{manifest}` and
`{content_state}` placeholders; `iiif.allowed_origins` limits which viewer sites can get tokens.

**Metadata.** Viewers show label, summary (in several languages), label/value pairs, rights (a Creative Commons or
RightsStatements.org URI), attribution, provider, date, languages, creators, contributors, subjects (optionally linked
to authorities such as Wikidata), identifiers and related links. Published custom fields ([API](api.md#fields)) are
label/value pairs too: a resource's in its Manifest, a collection's in its Collection; internal ones never are.

- Values that aren't set come from the recording itself: its title, date, language, speakers, main topics and summary.
- A namespace profile sets required fields, defaults, controlled vocabularies, and the default access and open parts.
- Every change is kept and can be reverted. Bulk edits report what would change before applying.

Manifests are checked against IIIF's Presentation 3 JSON Schema, bundled from IIIF's presentation-validator. This needs
`pip install jsonschema`. `GET /api/v1/resources/<id>/iiif` returns the manifest link, the recording's access, open
parts and whether it's published, the validation result and the viewer links.

## Embedding the player

```html
<iframe src="https://lens.example.org/embed/12?s=<share token>&t=90" style="width:100%;height:560px;border:0"></iframe>
```

The embed needs a share link (`?s=…`, from `POST /api/v1/resources/<id>/share`, which can be revoked) or a signed link from `GET /api/v1/resources/<id>/embed-link` (which expires). A share link's short address (`https://lens.example.org/s/<code>?t=90`) works as the `src` too. Only `/embed/<id>` and `/s/<code>` may be framed, and only by origins listed in `server.embed_frame_ancestors`. An expired or revoked link shows a neutral "This link isn't available" page in the frame (status 410). Share links count their plays and remember the sites that frame them (`GET /api/v1/resources/<id>/shares`). A host page can drive it:

```js
frame.contentWindow.postMessage({ type: 'archive:seek', t: 90, play: true }, '*')
```

It also accepts `archive:play` and `archive:pause`. Reports are self-contained HTML files with the same player. Audio is linked by relative path (`reports.audio: link`), inlined (`embed`) or left out (`none`).
