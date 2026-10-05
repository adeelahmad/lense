# Database: SurrealDB

Everything Lens stores lives in SurrealDB: recordings, transcript segments, speakers and voiceprints, entities and
the knowledge graph, full-text indexes, jobs, accounts, settings and audit.

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
    The embedded engine (SurrealDB 2.x inside the Python SDK) is slow to write and its full-text index loses entries
    when the database is closed and reopened. Lens repairs the index once per process, before the first search (the
    API does it at startup, `lens` commands that don't search skip it), which takes about 30 ms per transcript
    segment: seconds for a few thousand segments, but about ten minutes for 20,000. Beyond a few thousand segments,
    run a SurrealDB server; the Docker setups already do.

!!! warning "Use `surrealkv` (or RocksDB/TiKV) for servers, not `memory`"
    Under concurrent writes, SurrealDB 3.2.4's `memory` engine occasionally loses updates (we reproduced duplicate ids
    from an atomic counter). `surrealkv` is exact under the same load.

## Schema

The schema is defined idempotently in `app/domain/store.py` (`SCHEMA`) and applied on every start
(`DEFINE ... IF NOT EXISTS`), so new tables, fields and indexes need no migration (see [Upgrades](#upgrades) for the
rest). Tables are `SCHEMALESS` with indexes on the fields
that are queried. Graph edges are real relations: `mentions` (segment → entity) and `same_as` (speaker → speaker).

```sql
SELECT ->mentions->entity.name FROM segment WHERE recording = 12;
```

Word search (`search.engine`) is kept out of SurrealDB by default. With `sqlite`, the default, the text of transcript
lines, text on screen, file lines, objects and descriptions is indexed in a SQLite FTS5 file per database
(`<data_dir>/search/`, BM25 ranking, Porter stemming, highlights; `app/domain/textindex.py`). An event on each of
those tables notes changed rows in `text_change`, and the index catches up before each search, so writers need do
nothing. SurrealDB still decides which rows a person may see. On 10,000 transcript lines against a 3.2.4 server
(`surrealkv` storage) this used 64 MB of server memory instead of 380 MB, and writing and indexing them took about 5 s
instead of 10 s; the SQLite file was 4 MB.

With `surrealdb`, full-text indexes use 3.x's `FULLTEXT` syntax and fall back to 2.x's `SEARCH`, with a
`snowball(english)` analyser unless `search.stemming: none`. After changing either setting, run `lens reindex` (or
Settings › Search › Reindex now): it switches engines, taking the other one's index down.

Passages for search by meaning (`passage`) carry their vector in `embedding`, under an HNSW index (`passage_vec`,
cosine; servers only) defined when the first vector is stored, since its `DIMENSION` is the model's; `embedding_state:current` says
which model and dimension that is. A server searches it with `embedding <|k,ef|> $vec AND …filters`, which 3.2.4
applies inside the index scan (a filtered search still returns k rows).

## Upgrades

Installs keep their data across updates (running `install.sh` again, a new image, `git pull`). New tables, fields and
indexes arrive through `SCHEMA` on the next start. Anything else, such as rewriting stored values, moving data between
tables, or changing or removing an index that already exists, is a named step in `app/domain/migrations.py`:

* Every process that opens the database (the API, workers, `lens watch`, `lens` commands) runs the steps the database
  hasn't had yet when it starts, in order. One process takes a lock (`migration_lock:run`) and runs them; the others
  wait, so no code reads data in a shape it doesn't expect. A holder that dies loses the lock after five minutes.
* Before a database that holds data (any recording or account) runs pending steps, the process holding the lock backs
  it up into `<data_dir>/backups/before-upgrade-<time>` (see [Backups](#backups)); the newest three are kept. If the
  backup fails, for example because the disk is full, the upgrade doesn't start and Lens says why; set
  `LENS_UPGRADE_BACKUP=off` to upgrade without one.
* Each step run is recorded in `migration:⟨name⟩` with when and how long it took. A step that fails records its error
  and stops the start; once the cause is fixed, the next start runs it again.
* `lens migrations` lists every step (done, pending, failed, or unknown when a newer Lens ran it) without running
  anything, so it works while an upgrade is failing; `lens migrations --run` runs the pending ones.

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

## Concurrency

The handle (`store.DB`) is thread-safe. Against a server it keeps a small pool of connections; the embedded engine has
one connection that queries take turns on. SurrealDB transactions are optimistic: when two touch the same record at
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
* **The embedded full-text index loses postings on reopen** (the `SEARCH` index of the 2.x embedded engine): after a
  restart, searches silently missed most segments. `DB.ready_fulltext()` rebuilds it once per process before the
  first full-text query; `tests/domain/test_search_index.py` covers it. Servers (3.x `FULLTEXT`) are unaffected.
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

`lens backup` writes one into `<data_dir>/backups` (Lens also takes one before upgrading the database):

* With a server, `manual-<time>.surql.gz`: SurrealDB's export of the database, gzipped. Restore it into an empty
  database with `gunzip -k` and `surreal import --conn http://host:8000 --user root --pass … --ns archive --db main
  <file>.surql`. Taking one by hand: `surreal export --conn http://host:8000 --user root --pass … --ns archive --db main
  backup.surql`.
* Embedded, `manual-<time>.surrealkv`: a copy of the database folder, taken with it closed. Restore it by stopping Lens
  and putting the copy in place of `<data_dir>/surrealdb`.

Also back up `data_dir` (reports, frames, caches, and `secret.key` unless `ARCHIVE_SECRET_KEY` is set; without that
key, stored credentials can't be decrypted).
