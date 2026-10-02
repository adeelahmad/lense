# MCP: the archive for assistants

Lens is a [Model Context Protocol](https://modelcontextprotocol.io) server: an assistant such as Claude or Cursor
connects to it and searches the archive, reads transcripts and documents, finds its way through namespaces and
collections, and imports and processes things, as the person who connected it.

* **Address:** `https://<your Lens>/mcp` (streamable HTTP). The web app shows it under API tokens → Apps with access.
* **Sign-in:** OAuth ([Authentication](authentication.md#oauth)): the assistant registers itself, sends you to Lens to
  sign in and allow it, and gets tokens that act as you. An API key works too (`Authorization: Bearer la_…`), for
  clients without OAuth.
* **What it may do:** exactly what you may. Every tool is a request to the API's own routes with your token, so
  namespaces and collections you have no role in aren't there for it, a viewer's assistant can't import, a read-only
  token is offered no tools that change things, and what it does is in the audit log as yours.

## Connecting

**Claude (claude.ai, the desktop and mobile apps):** Settings → Connectors → Add custom connector, and give it the
address. Claude opens Lens's consent page: sign in, choose whether it may make changes, Allow.

**Claude Code:**

```bash
claude mcp add --transport http lens https://lens.example.org/mcp
```

then `/mcp` in a session to sign in. With an API key instead:

```bash
claude mcp add --transport http lens https://lens.example.org/mcp --header "Authorization: Bearer la_…"
```

**Cursor** (`~/.cursor/mcp.json`, or `.cursor/mcp.json` in a project):

```json
{ "mcpServers": { "lens": { "url": "https://lens.example.org/mcp" } } }
```

Cursor signs in through the browser the same way; add `"headers": {"Authorization": "Bearer la_…"}` to use an API
key.

**Anything else that speaks MCP** needs the address and, for OAuth, nothing more: `POST /mcp` without a token answers
401 with `WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource/mcp"`, which leads the
client to the authorization server's metadata, registration and the consent page.

Take an assistant's access away under API tokens → Apps with access (or revoke the API key).

## Tools

| Tool | |
|---|---|
| `list_namespaces` | the namespaces you can see, your role in each, how many resources |
| `list_collections` | a namespace's collections, nested, with how many resources each holds |
| `list_resources` | resources, newest first: by namespace, collection (with the ones inside it), kind, tag, words in the title, dates |
| `search` | moments by what was said, shown on screen or written on a page; with `meaning` (the default) also passages that say the same in other words, where [search by meaning](configuration.md#search-by-meaning) is on. Filters: namespace, resource, speaker, emotion, object |
| `get_resource` | one resource: what it is, where it lives, speakers, summary, chapters, how many lines or pages |
| `get_transcript` | a recording's lines with who said them and when, all or between two times; long ones in parts |
| `get_pages` | the text of a document's or an image's pages, and what each shows where that was described |
| `get_job` | how a processing job is going |
| `import_text` | add a transcript or notes as a new resource (editors) |
| `import_web_page` | keep a public web page as a document (editors; where the server can capture pages) |
| `process` | queue resources for their pipeline, or for the steps named: `embed` and `analyze` index them for search (editors) |

Times are seconds from the start; pages count from 1. A tool that can't do what was asked (an id that isn't there
for you, a role you don't have) says so in its result, with the API's reason and status.

## Resources

Each resource's text is an MCP resource, `lens://resource/<id>`: a transcript as lines with times and speakers, a
document page by page, as Markdown. `resources/list` lists what you can read, newest first, fifty at a time;
`resources/read` reads one.

## How it works

`POST /mcp` takes one JSON-RPC message and answers with JSON; there is no server-sent stream (`GET /mcp` answers 405)
and no session, so it works behind any proxy and with several API processes. It speaks protocol versions 2025-06-18,
2025-03-26 and 2024-11-05. The web app serves `/mcp` and `/.well-known/…` on its own address, so the address people
use is the only one a client needs.
