# MCP server

Lens is a [Model Context Protocol](https://modelcontextprotocol.io) server, so agents (Claude, Cursor, VS Code,
ChatGPT and other MCP clients) can search the archive, read recordings and cite them. An agent signs in as a person
and sees exactly what that person sees: their namespaces, the collections they were given a role on, and the graphs
those open. Everything it can do only reads.

## Connecting

The server is at `/mcp` on the web app's address: `https://lens.example.org/mcp` (`http://localhost:3000/mcp`
locally). Add it to the client as a remote (Streamable HTTP) server, and the client sends you to Lens to sign in.

| Client | |
|---|---|
| Claude (claude.ai, desktop) | Settings → Connectors → Add custom connector, with the address |
| Claude Code | `claude mcp add --transport http lens https://lens.example.org/mcp` |
| Cursor | `~/.cursor/mcp.json`: `{"mcpServers": {"lens": {"url": "https://lens.example.org/mcp"}}}` |
| VS Code | `.vscode/mcp.json`: `{"servers": {"lens": {"type": "http", "url": "https://lens.example.org/mcp"}}}` |
| MCP Inspector | `npx @modelcontextprotocol/inspector`, transport Streamable HTTP |

Signing in is OAuth ([Authentication](authentication.md#oauth)): the client finds out where to sign in from the 401 it
gets first (`WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource/mcp"`), registers
itself, and opens Lens's consent page, where you sign in if you aren't and choose Allow. Read access is all it needs.
A token an app asked for another server (its `resource`, RFC 8707) doesn't work here; one for Lens or for its
`/mcp` is fine. The client renews its token by itself; you see it under API tokens → Apps with access, and take its access away
there.

A client that can't sign in can send an API token instead (`Authorization: Bearer la_…`, made under API tokens), for
example `claude mcp add --transport http lens https://lens.example.org/mcp --header "Authorization: Bearer la_…"`.

For the addresses to be right, set `FRONTEND_URL` to the address people use, and list the web app as a trusted proxy
when it runs on another machine or container ([Configuration](configuration.md#trusted-proxies)); a reverse proxy in
front of the web app also needs `TRUST_PROXY_HEADERS=true` ([Authentication](authentication.md#oauth)).

## Tools

| Tool | |
|---|---|
| `search` | moments where words are said, shown on screen or written, best first: the recording, speaker, time, the matching text (matches in **bold**) and a link to that moment. Words, `"phrases"`, `OR`; by namespace, speaker or recording |
| `list_namespaces` | the namespaces you can read, with their recordings, hours, speakers and your role |
| `list_recordings` | recordings, newest first, by namespace, title words, dates, speaker, tag or kind |
| `list_speakers` | the speakers of your namespaces, the ones who talk most first |
| `get_recording` | one recording: when, how long, speakers, summary and key points, chapters, the entities and keywords most mentioned, its link |
| `get_transcript` | a recording's lines in order (speaker, time, text, link), a page at a time, from a line or a time window |
| `fetch` | a recording's whole text at once, as `[m:ss] Speaker: …` lines |
| `cite` | a citation for a moment (a line or a second, and a few lines from there): the words, who said them, the recording and date, the time, the link, and all of it as Markdown |
| `list_entities` | people, organisations, products, places, events, works and terms mentioned, by name, type, namespace or recording |
| `get_entity` | one entity: its names, how often and when it is mentioned, what it is mentioned with, who mentions it most, and the lines that mention it |
| `explore_graph` | the knowledge graph around an entity (`e12`) or a speaker (`s3`), one or two steps out |
| `find_path` | the shortest chain of links between two of them, with lines that show each link |
| `graph_schema` | what the property graph holds (labels, relationships, properties, counts) and example Cypher ([The graph](graph.md)) |
| `graph_query` | read-only Cypher over the graph of one namespace or every shared one: columns, rows, and the nodes and relationships found |
| `graph_related` | a node's parents, children, ancestors, descendants or neighbours |
| `graph_paths` | the paths between two nodes, shortest first |
| `list_topics` | each namespace's vocabulary of topics ([Topics](topics.md)), by any label, top topics or the narrower ones of a topic |
| `get_topic` | one topic: its labels, definition, broader, narrower and related topics, and the recordings about it, with links |
| `suggest_topic` | suggest that recordings are about a topic; it waits for someone to accept it. Needs a write-scope token and editor access |
| `find_notes` | [notes](notes.md) by words in their title, summary or text, or by where they're filed (PARA), with links |
| `read_note` | a note's Markdown with its links and backlinks, by id or as the page of a thing (`recording:12`) |
| `write_note` | write a note or a thing's page, marked as written by an assistant. Needs a write-scope token and editor access |
| `file_storage` | where Lens keeps its own files (notes' images and attachments) and the connections it could use; `check` tests it. Admins only |
| `set_file_storage` | keep new files on this machine or a storage connection (folder, rclone crypt); checked before it's saved. Admins only, with a write-scope token |
| `propose_graph_change` | ask for two entities to be merged or linked; it waits in Proposed changes unless `apply` makes it at once (it can be undone). Needs a write-scope token and editor access |

Results are JSON (as `structuredContent`, and the same as text). Links open the recording's page in the web app at
that moment (`/resources/<id>?t=<seconds>`), on the address the client used. Mistakes the agent can fix (a wrong
argument, something it can't read) come back as a tool result with `isError`, so it can try again; what isn't the
person's to read answers "not found", as the API does. `search` and `fetch` follow the shape ChatGPT's connectors
expect (`results` with `id`, `title`, `url`; `fetch` by that `id`).

Every tool reads, except `suggest_topic`, which adds suggestions people accept or dismiss, `propose_graph_change`,
which records an undoable change (proposed by default), `write_note`, which adds a note, and `set_file_storage`,
which changes where new files are kept (admins).
Importing, editing and curating stay in the web app and the API.

## Protocol

Streamable HTTP without sessions: every request is a `POST /mcp` answered with JSON on the same response. `GET` and
`DELETE` (a stream from the server, ending a session) answer 405. Both eras of the protocol are served:

* the `initialize` handshake (versions 2024-11-05 to 2025-11-25), with `MCP-Protocol-Version` on the requests after
  it; notifications get 202, and a 2025-03-26 client may batch;
* 2026-07-28's per-request envelope: no handshake, the version and the client's capabilities in each request's
  `params._meta`, repeated with the method (and the tool's name) in the `MCP-Protocol-Version`, `Mcp-Method` and
  `Mcp-Name` headers; `server/discover` lists the versions. Results carry `resultType`, and protocol errors their
  HTTP status (400, or 404 for an unknown method).

The `MCP-Protocol-Version` header decides the era: none or a handshake version is the first, anything else the
second, where a version Lens doesn't speak is answered `-32022` with the ones it does. Requests from a browser must
come from the web app's own address or one in `CORS_ORIGINS` (403 otherwise), which keeps other sites from using a
signed-in browser to reach it. A message may be 1 MB at most (413), and a batch 20 messages. Implemented in `fastapi_backend/app/api/mcp.py` (protocol) and `mcp_tools.py` (tools);
the tests run the official MCP SDK's client against it in both eras.
