# IIIF and descriptive metadata

Every recording is published as a IIIF Presentation 3.0 Manifest, and every namespace as a Collection. Any IIIF viewer
can open them, and harvesters can follow them.

- **Manifest** (`/iiif/<id>/manifest`): the audio on a Canvas with its duration.
  - The transcript comes as WebVTT captions plus per-line annotations; speakers and entities come as tagging annotations.
  - Chapters become the table of contents (Ranges).
  - Downloads (`rendering`): vtt, srt, txt, md and json.
  - A schema.org record and a Dublin Core record, linked with `seeAlso`.
- **Collections:** `/iiif/collection` and `/iiif/collection/<namespace>`.
- **Content Search 2.0:** `/iiif/<id>/search` and `/autocomplete`, plus `/iiif/collection/<namespace>/search`. Hits come
  with highlighting (TextQuoteSelector).
- **Content State 1.0:** `GET /api/v1/recordings/<id>/content-state?t0=&t1=` gives a link to an exact moment, encoded the
  way the spec requires, that compatible viewers open.
- **Change Discovery 1.0:** `/iiif/discovery/activity`, a feed of Create, Update and Delete events for published
  recordings.
- **Authorization Flow 2.0:** protected audio and transcripts carry a probe service.
  - Other viewers open `/iiif/auth/access` to sign in and get a token from `/iiif/auth/token`, which posts it only to
    the viewer's origin.
  - A successful probe returns a short-lived signed link, so playback doesn't depend on third-party cookies.
  - `/iiif/auth/logout` revokes the tokens.
- **Import:** `POST /api/v1/import/iiif` (admins) takes a Presentation 3 Manifest or Collection from another server.
  - It copies the audio, keeps WebVTT captions as the transcript (speakers included), and maps the metadata.
  - It then queues the namespace's pipeline, skipping transcription.

Access decides what is published. It's set per recording, with a default per namespace:

- `public`: everything is open.
- `transcript`: the transcript is open; the audio needs sign-in.
- `signed-in`: the metadata is open; the audio and transcript need sign-in.
- `private` (the default): not published at all.

Set `iiif.base_url` to the stable public HTTPS address, since identifiers are built from it. Put the server behind
HTTPS before publishing: the authorization flow requires it, and its cookie is `SameSite=None; Secure`. Other IIIF
viewers also need the public host in `server.allowed_hosts`. `iiif.viewers` holds "Open in" links, using `{manifest}` and
`{content_state}` placeholders; `iiif.allowed_origins` limits which viewer sites can get tokens.

**Metadata.** Viewers show label, summary (in several languages), label/value pairs, rights (a Creative Commons or
RightsStatements.org URI), attribution, provider, date, languages, creators, contributors, subjects (optionally linked
to authorities such as Wikidata), identifiers and related links.

- Values that aren't set come from the recording itself: its title, date, language, speakers, main topics and summary.
- A namespace profile sets required fields, defaults, controlled vocabularies and the default access.
- Every change is kept and can be reverted. Bulk edits report what would change before applying.

Manifests are checked against IIIF's Presentation 3 JSON Schema, bundled from IIIF's presentation-validator. This needs
`pip install jsonschema`. `GET /api/v1/recordings/<id>/iiif` returns the manifest link, its access level, the validation
result and the viewer links.

## Embedding the player

```html
<iframe src="https://lens.example.org/embed/12?t=90" style="width:100%;height:560px;border:0"></iframe>
```

Only `/embed/<id>` may be framed, and only by origins listed in `server.embed_frame_ancestors`. A host page can drive it:

```js
frame.contentWindow.postMessage({ type: 'archive:seek', t: 90, play: true }, '*')
```

It also accepts `archive:play` and `archive:pause`. Reports are self-contained HTML files with the same player. Audio is linked by relative path (`reports.audio: link`), inlined (`embed`) or left out (`none`).
