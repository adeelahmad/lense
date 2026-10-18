# Database: SurrealDB

Everything Lens stores lives in SurrealDB: assets, text segments, people and voiceprints, entities and
the knowledge graph, jobs, accounts, settings and audit. The word search index is kept beside it, in a SQLite file by
default ([below](#schema)).

## Two ways to run it

| `SURREAL_URL` | Mode | Use it for |
|---|---|---|
| unset (or `surrealkv://path`) | **Embedded** in the API process, data under `data_dir/surrealdb` | a single machine, trying Lens out. Only one process can open it, so run workers inline (`RUN_BACKGROUND=true`) |
| `ws://host:8000` | **SurrealDB server** | anything shared: the API, several workers, other machines |
| `mem://` | in memory | tests |

```bash
SURREAL_URL=ws://127.0.0.1:8000 SURREAL_USER=root SURREAL_PASS=… SURREAL_NS=archive SURREAL_DB=main
SURREAL_POOL_SIZE=8     # connections per process (servers only)
```

The Docker setups run SurrealDB 3.2.4 on the `surrealkv` storage engine. Lens is tested with the Python SDK's embedded
engine and with SurrealDB 2.3 and 3.2 servers.

!!! note "Embedded mode is for trying Lens and small archives"
    The embedded engine (SurrealDB 2.x inside the Python SDK) is slow to write. With `search.engine: surrealdb`, its
    full-text index also loses entries when the database is closed and reopened. Lens then repairs the index once per
    process, before the first search (the API does it at startup, `lens` commands that don't search skip it), which
    takes about 30 ms per segment: seconds for a few thousand segments, but about ten minutes for 20,000.
    The default word index (SQLite) isn't affected. Beyond a few thousand segments, run a SurrealDB server; the Docker
    setups already do.

!!! warning "Use `surrealkv` (or RocksDB/TiKV) for servers, not `memory`"
    Under concurrent writes, SurrealDB 3.2.4's `memory` engine occasionally loses updates (we reproduced duplicate ids
    from an atomic counter). `surrealkv` is exact under the same load.

## Schema

The schema is defined idempotently in `app/domain/store.py` (`SCHEMA`) and applied on every start
(`DEFINE ... IF NOT EXISTS`), so new tables, fields and indexes need no migration (see [Upgrades](#upgrades) for the
rest). Tables are `SCHEMALESS` with indexes on the fields
that are queried. Graph edges are real relations: `mentions` (segment → entity) and `same_as` (person → person).

```sql
SELECT ->mentions->entity.name FROM segment WHERE recording = 12;
```

Word search (`search.engine`) is kept out of SurrealDB by default. With `sqlite`, the default, the text of asset
lines, text on screen, file lines, objects and descriptions is indexed in a SQLite FTS5 file per database
(`<data_dir>/search/`, BM25 ranking, Porter stemming, highlights; `app/domain/textindex.py`). An event on each of
those tables notes changed rows in `text_change`, and the index catches up before each search, so writers need do
nothing. SurrealDB still decides which rows a person may see. On 10,000 lines of text against a 3.2.4 server
(`surrealkv` storage) this used 64 MB of server memory instead of 380 MB, and writing and indexing them took about 5 s
instead of 10 s; the SQLite file was 4 MB.

With `opensearch`, the same words go to an OpenSearch (or Elasticsearch-compatible) cluster at
`search.opensearch_url` (with `opensearch_user`, `opensearch_password` and `opensearch_verify` when it needs them):
an index per table named `lens-<namespace>-<database>-<table>`, with the `english` analyser unless
`search.stemming: none`. It suits an archive too big for one small machine, with OpenSearch on another one. When the
cluster can't be reached, searches read the tables instead and say nothing is wrong, so Lens keeps working. The
`opensearch` profile of the production compose file (`docker compose -f docker-compose.prod.yml --profile opensearch
up -d`) runs one next to Lens at `http://opensearch:9200`.

With `surrealdb`, full-text indexes use 3.x's `FULLTEXT` syntax and fall back to 2.x's `SEARCH`, with a
`snowball(english)` analyser unless `search.stemming: none`. After changing either setting, run `lens reindex` (or
Settings › Search › Reindex now): it switches engines, taking the other one's index down.

Passages for search by meaning (`passage`) carry their vector in `embedding`, under an HNSW index (`passage_vec`,
cosine; servers only) defined when the first vector is stored, since its `DIMENSION` is the model's; `embedding_state:current` says
which model and dimension that is. A server searches it with `embedding <|k,ef|> $vec AND …filters`, which 3.2.4
applies inside the index scan (a filtered search still returns k rows).

The stream of activities automations follow ([Automations](automations.md#contract)) is mostly a view over rows
kept anyway, such as `recording.created_at`. What no row records goes in `event_log:<n>` (by `logged_at` and
`published`, kept for `events.keep_days`), with the keys it was emitted with in `event_log_key:<hash>`. Each consumer
of the stream keeps where its next look starts in `event_cursor:<consumer>` (a position per source), and claims what
it handles in `event_claim:<hash of consumer and key>`; a claim is a `CREATE` only one process wins, and claims go
after a day. Each person's review marks on the timeline ([Timeline](timeline.md#review-marks)) are
`event_mark:<hash of account and activity>`, a row only while one of its flags is set. An automation trigger's
webhook is `automation_hook:<n>` (its automation and trigger, the hash of its token, its signing secret sealed).
Automations' timers ([Time](automations.md#time)) are `timer:<hash of key>` (by `status` and `at`, and by
`automation`); one that ended goes after 7 days. Saved variables ([Saved variables](automations.md#saved-variables))
are `automation_var:<hash of scope and name>` (`automation` or `space`, `name`, `value`, `rev` for writes that
race, `untrusted`, who set it and when). So that it can be read
without opening files, a resource made from an email keeps the message's addressing as it was listed
(`recording.mail`) and one made from a calendar event its times, place and people (`recording.event`); both are
optional, and older resources simply don't have them.

## Upgrades

Installs keep their data across updates (running `install.sh` again, a new image, `git pull`). New tables, fields and
indexes arrive through `SCHEMA` on the next start. A field that is only added needs no step when its readers treat it
as missing on older rows: a chat message's record (`meta`,
[What each answer records](assistant.md#what-each-answer-records)) is one, and messages from before it simply have
none. [Deep research](assistant.md#deep-research) is another: its tables (`research`, and `research_run` for the claim
of the process running one) are new, a message's `kind` is missing on older ones, and notes accept `chat` links
without rewriting any. Anything else, such as rewriting stored values, moving data between
tables, or changing or removing an index that already exists, is a named step in `app/domain/migrations.py`:

* Every process that opens the database (the API, workers, `lens watch`, `lens` commands) runs the steps the database
  hasn't had yet when it starts, in order. One process takes a lock (`migration_lock:run`) and runs them; the others
  wait, so no code reads data in a shape it doesn't expect. A holder that dies loses the lock after five minutes.
* Before a database that holds data (any asset or account) runs pending steps, the process holding the lock backs
  it up into `<data_dir>/backups/before-upgrade-<time>` (see [Backups](#backups)); the newest three are kept. If the
  backup fails, for example because the disk is full, the upgrade doesn't start and Lens says why; set
  `LENS_UPGRADE_BACKUP=off` to upgrade without one.
* Each step run is recorded in `migration:⟨name⟩` with when and how long it took, and the process that ran it says so
  in its log (`upgrading the database: <name>`), whether that is the API or a worker (`docker compose logs backend
  worker`). A step that fails records its error and stops the start; once the cause is fixed, the next start runs it
  again.
* `lens migrations` lists every step (done, pending, failed, or unknown when a newer Lens ran it) without running
  anything, so it works while an upgrade is failing; `lens migrations --run` runs the pending ones.

The steps so far, in the order they run:

* `access-levels`: IIIF-only access levels (public, transcript, signed-in, private) become the access setting.
* `collection-homes`: every namespace gets its default collection, and every asset a home in one.
* `tour-seen`: people who signed in before the welcome tour existed don't get it.
* `document-read-step`: documents and images queued to be transcribed are read instead, and failed ones run again.
* `chat-tree`: conversations become trees of versions ([Versions](assistant.md)): each message of a conversation
  (`chat_message`) gets the one before it as its `parent`, and the conversation (`chat`) its last message as its
  `head`. Only conversations without a `head` are touched, so it's safe to run twice; until it has run, a conversation
  without a `head` is read as one line, and saved as one before anything is added to it.
* `mcp-sealed-secrets`: the tokens and keys MCP servers kept in plain text in their headers and env are sealed,
  in every version ([Adding an MCP server](assistant.md#adding-an-mcp-server)); sealed ones are left as they are.

To add a step:

1. Write a function `fn(db)` next to the code it serves. It must be safe to run twice, since a start that stops half
   way runs it again; prefer `UPDATE ... WHERE <old shape>` over rewriting every row.
2. Append `("short-name", module.fn)` to `steps()` in `migrations.py`, and the name to `SHIPPED` in
   `tests/domain/test_migrations.py`. Never rename, reorder or remove a shipped step: databases remember steps by
   name, so a renamed one runs again and a removed one never runs.
3. Keep reads tolerant of the old shape until the step has run (it runs before requests are served, but a worker on
   another machine may still be on the old version).
4. Test it on data in the old shape, against a SurrealDB server too (`LENS_TEST_SURREAL_URL`);
   `test_every_step_is_safe_to_repeat` checks that a second run changes nothing.

### Checked before every release

Every release is checked by upgrading the one before it, holding data, to it: the Release workflow runs the
**Upgrade** workflow (`.github/workflows/upgrade.yml`) first and cuts nothing when it fails. It also runs on pull
requests that change the backend, and by hand from any version (Actions > Upgrade > Run workflow). It runs
`ci/upgrade/upgrade.sh`, which you can run yourself with Docker and uv (`ci/upgrade/upgrade.sh v0.12.0` starts from an
older version; `ci/local-ci.sh` runs it too):

1. The last release (the newest `vX.Y.Z` tag) starts as the production stack runs it, the API and a worker on the
   SurrealDB server its `docker-compose.prod.yml` names, with an empty database.
2. It's seeded through its API (`ci/upgrade/lens_upgrade.py seed`): two transcripts and their analysis, an entity, a
   note and a comment on an asset, a notes page, a conversation, an MCP server with a token, an API key and settings.
   What the API shows of them is kept.
3. This code starts on the same database and data folder (and on its own SurrealDB version when that changed), the API
   and a worker at once, as after an upgrade.
4. It fails when the API shows any of the seeded data differently or the admin's password or the API key no longer
   signs in; when any record in the database is gone; when a step that was pending didn't start exactly once (in the
   API's and the worker's logs together), failed, or ran without a backup taken first; or when running every step
   again changes anything (the steps the upgrade ran must change nothing, and no step may change anything the second
   time it runs).

The seed is written against the API of the version it seeds, so it runs from the release being upgraded when that
release has it. When you change an endpoint it uses, change `seed` and `facts` in `lens_upgrade.py` with it; when you
add data worth keeping across upgrades, add it to both. The logs, the seeded facts and the database before and after
are in `.upgrade-check/` (on GitHub, the run's `upgrade-check` artifact).

## Concurrency

The handle (`store.DB`) is thread-safe. Against a server it keeps a small pool of connections; the embedded engine has
one connection that queries take turns on. A query made on a thread that is already inside one (Python closing an
abandoned generator in the middle of a query, say) shares that thread's connection rather than waiting for it.
SurrealDB transactions are optimistic: when two touch the same record at
once, one fails with a retryable write conflict. The handle retries those automatically, with jittered backoff, for
single statements and for `run()` transactions (which roll back as a whole, so retrying is safe).

## Traps the code avoids

* **Composite indexes on 2.x.** A `(space, x)` index makes lookups by `space` alone return nothing on 2.x, so
  uniqueness is enforced with single `space:value` key fields instead.
* **`CONTAINS` on indexed fields (2.x)** returns nothing; use `string::contains()`.
* **Delete and re-create in one transaction (3.2).** A transaction that deletes a record and re-creates the same id
  loses it silently (and `INSERT` fails with "already exists"). Re-transcription overwrites segments in place and
  deletes only the extras.
* **Multi-statement `query()` in the Python SDK** only checks the first statement's result. Transactions go through
  `DB.run()`, which checks every statement.
* **The embedded full-text index loses postings on reopen** (the `SEARCH` index of the 2.x embedded engine, used only
  with `search.engine: surrealdb`): after a restart, searches silently missed most segments. `DB.ready_fulltext()`
  rebuilds it once per process before the first full-text query; `tests/domain/test_search_index.py` covers it.
  Servers (3.x `FULLTEXT`) are unaffected.
* **The embedded engine's vector index drops filtered rows**: with a KNN operator plus a parenthesised or OR
  filter, or sometimes just two filters, 2.x returned nothing. On the embedded engine, search by meaning compares the
  query with every passage that passes the filter (`vector::similarity::cosine`) instead, which is exact and takes
  about a second per 5k passages searched; servers use the index. The embedded engine gets no HNSW index at all,
  since inserting into one there is slow (minutes for 20k passages) and nothing would read it.
* **`NONE` drops fields.** Settings saved in the app are stored as JSON text, so "cleared" survives.
* **`count()` with an `OR … = NONE` filter (embedded 2.x)** counts rows twice (`status IN $s OR status = NONE` gave
  10 for 4 rows), although the rows themselves come back right. Totals count the selected ids instead:
  `array::len((SELECT VALUE id FROM recording WHERE …))`, which is exact on both engines.

## Backups

**Settings › Backups** in the web app takes a backup now, lists them, downloads one, restores one and takes them on a
schedule (the API: `/api/v1/admin/backups`); `lens backup` takes one from the command line, and an upgrade takes one
first ([Updates and backups](updates.md)). Each is one entry in `<data_dir>/backups`, named
`<kind>-<time>`:

| Kind | Taken | Kept |
| --- | --- | --- |
| `manual` | from Settings › Backups, `POST /api/v1/admin/backups` or `lens backup` | until you delete it |
| `scheduled` | on `backups.schedule` (any cron expression in `backups.timezone`, typed or picked in one click; none by default) | the newest `backups.keep` (7), here and on the connection |
| `before-upgrade` | before the database is upgraded, and before a release is installed | the newest three taken before database upgrades |
| `before-restore` | what the database held just before a restore replaced it | the newest three |

* With a server, `<kind>-<time>.surql.gz`: SurrealDB's export of the database, gzipped.
* Embedded, `<kind>-<time>.surrealkv`: a copy of the database folder, taken with it closed.
* With either, the files Lens keeps under `data_dir` (uploads, `objects`, media, frames, renditions, web pages,
  reports, exports, podcasts, certificates) in `<data_dir>/backups/.files/<name>/`. A file that hasn't changed since
  the previous backup is a hard link to that backup's copy, so each backup takes only the room of what changed. Left
  out: what Lens fetches or rebuilds by itself (`models`, `bin`, `python`, `cache`, the search index), passing files
  (`tmp`, `locks`, `logs`, unfinished uploads), `secret.key`, the database's own folder (`surreal` in a microsandbox:
  the export holds it), and hidden entries at the top of `data_dir` (in a microsandbox, `.lens` is the Lens it runs,
  and the rest are caches). A file Lens can't read isn't its own: it is left out, with a warning in the log. A backup
  is refused when the disk hasn't room for the files that changed. A backup with files, or of an embedded database,
  downloads as one `.tar.gz` holding `<name>` and `<name>.files/`. Backups from before files were kept show *database
  only* in Settings.

A backup taken in Settings, through the API or on the schedule is also a `backup.finished` activity for automations,
and a scheduled one that failed says why ([Automations](automations.md#lens-itself-as-activities)).

**Restoring** replaces the whole database, and the files under `data_dir`, with the backup's (a *database only*
backup leaves the files as they are). What was there is backed up first (`before-restore`) and
put back if the restore fails; a backup from an older Lens is brought up to date (schema and upgrade steps) afterwards.
People signed in since the backup was taken sign in again. Only an admin signed in with a passkey can download or
restore a backup from Settings (a download there is a link that works for two minutes); an admin API token can take,
list and delete them. `backups.restore(db, cfg, name)` in `app/domain/backups.py` is the one restore everything uses.

**A copy somewhere else.** Choose a storage connection under *Also copy each backup to* (S3 or S3-compatible,
Google Drive, Dropbox, OneDrive, SFTP, SMB, WebDAV or a folder; added under Sources, through rclone), and every new
backup, by hand or scheduled, is copied there under `backups.folder` as `<name>.lensbk`, with the scheduled ones
kept to `backups.keep` there as well. A copy is encrypted (AES-256-GCM) with a key derived from `secret.key` (or
`ARCHIVE_SECRET_KEY`), not with a key kept in the database, so it can be opened after the database is lost. Keep that
key away from the connection. *Copy there* sends one again, and *Bring back* fetches a copy that only the connection
has, so you can then restore or download it. If copying fails, the backup here is still kept, and Settings shows why.

By hand, with a server: `gunzip -k` the file, then `surreal import --conn http://host:8000 --user root --pass … --ns
archive --db main <file>.surql` into an empty database. Embedded: stop Lens and put the copy in place of
`<data_dir>/surrealdb`. Then copy the contents of `<name>.files/` into `data_dir`.

Keep `secret.key` (unless `ARCHIVE_SECRET_KEY` is set) apart from the backups: without it, stored credentials and server-held file keys
can't be decrypted, and with it a backup opens them ([encryption](encryption.md)).
