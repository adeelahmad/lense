<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/lens-horizontal-dark.svg">
    <img src="docs/brand/lens-horizontal-light.svg" alt="Lens" width="360">
  </picture>
</p>

# Lens: intelligence for your agent

**Safe, secure and cost-effective.**

Connect your live data streams; Lens turns them into a cited knowledge graph your agent can reason over, through MCP,
a REST API or its own assistant. It runs on your hardware with local models or any cloud model, private by default and
cost-aware.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/adeelahmad/lense/main/install.sh | sh
```

That one line is all: it opens an installer in your browser that checks the computer, then either installs with the
defaults in one click (with a model server it finds on the computer) or asks how Lens should run (in Docker or a
[microsandbox](docs/microsandbox.md) microVM), the AI model and its key and any extras. Either way it installs what's
missing (Docker included), starts Lens and opens its setup page ([details](docs/get-started.md#the-web-installer)).
Run it again to update. On Windows, run `irm https://raw.githubusercontent.com/adeelahmad/lense/main/install.ps1 | iex`
in PowerShell; or skip the terminal and double-click the launcher for your system from the
[latest release](https://github.com/adeelahmad/lense/releases/latest).
[Deployment](docs/deployment.md) covers servers and Cloudron; there are also packages for
[Synology](packaging/synology/README.md), [QNAP](packaging/qnap/README.md) and [Proxmox](proxmox/README.md).

To work on the code instead, `make run` builds and runs everything from a checkout, `make setup-code` prints the
first-admin setup code, and `make dev` runs it with hot reload; see [Get started](docs/get-started.md).

## Why Lens

**AI doesn't lack intelligence. It lacks structure. Lens gives your agent eyes.** Without structure, every prompt is a
guess about who you are and what happened. Lens supplies the people and things involved, how they relate, what happened
when, and the source of each fact, so your agent stops guessing.

Lens is one private AI hub for your whole life. Every source is a sensor: recordings, documents, email, calendars,
web pages, chat rooms, devices and webhooks all flow in, and everything becomes memory you can trust, because every
answer cites where it came from, down to the second of a recording or the line of a document. It runs on your own
hardware, from a Raspberry Pi to the cloud, and you own every byte.

Lens is built first for agents and AI assistants. People talk to their agent in chat, by voice, or in Slack, WhatsApp
and other chat rooms; the web app is for setup, settings, pipelines and workflows, review, debugging and the charts
that show what is in the archive and what it cost.

Lens is also infrastructure to build on. An app built on Lens, such as an email responder or a customer manager,
starts with real, structured context (who is involved, what was said, what was decided, with sources) instead of a
blank prompt. Apps reach it the same way agents do: through the MCP server, the REST API and OAuth sign-in, limited to
what their person may read.

## What you can do with it

Everything below is built and merged. Work that is still in a pull request is listed under [In progress](#in-progress).

- **Connect your agents.** Lens is an MCP server (`/mcp`): agents search, read transcripts and notes, cite exact
  moments, find similar content, explore the graph with Cypher or SPARQL or browse it as folders and files, suggest
  topics and graph changes, write notes and make podcasts. They sign in with OAuth and see only what their person may
  read. The web app runs on the same REST API, so an agent can do what a person can. ([MCP](docs/mcp.md),
  [API](docs/api.md), [the graph as files](docs/file-tree.md), [authentication](docs/authentication.md))
- **Ask, and get cited answers.** A chat assistant on every page, and by voice, searches your archive by words (and by
  meaning, once turned on), reads it, cites it and acts on it with tools: changes wait for your approval. It reads
  answers aloud with a speech provider, macOS `say` or StyleTTS2 on this machine (opt-in), and `/clear` and
  `/compact` keep long conversations short. Each namespace can have its own assistant with its own name, instructions
  and memory, answering in Slack, Discord, Telegram, Matrix, WhatsApp and other chat rooms through Matterbridge.
  ([Chat](docs/assistant.md), [chat rooms](docs/chat-rooms.md), [speech providers](docs/speech-providers.md))
- **Extend the assistant.** Add tools (a prompt, a web request, a graph drawn on the canvas, or sandboxed Python),
  skills, hooks and remote MCP servers, by code, on the canvas or just by asking. Install skills and plugins from
  marketplaces in the open plugin format, or from a repository or an archive.
  ([Extending the assistant](docs/assistant.md#extending-the-assistant-tools-skills-hooks-and-plugins))
- **Bring in everything.** Watched folders on S3, Dropbox, Google Drive, OneDrive, SFTP, SMB, WebDAV or local disks;
  IMAP mail and iCal calendars; a built-in MQTT broker, syslog and webhook streams; import webhooks for scanners and
  phone shortcuts. Upload audio, video, PDFs, Office files, photos, JSON and zip or tar archives; capture web pages one
  at a time, from a link list or a bookmarks file, by API or by asking the assistant; and import videos and podcasts
  by their address (off by default). ([Sensors](docs/sensors.md), [processing](docs/processing.md),
  [import webhooks](docs/api.md#import-webhooks))
- **Clip from the browser.** The Lens web clipper for Chrome, Edge, Firefox and Safari turns a page, a selection or a
  part you pick into Markdown, lets you look it over, and sends it to a namespace. New releases reach installed copies
  through the browser's store or a self-hosted update file. In Chrome and Edge, its side panel is a chat with the
  assistant, which can also use that browser on the sites you allow (open, read, click, type), asking before anything
  that sends, deletes, buys or posts. ([Web clipper](docs/web-clipper.md),
  [the assistant in your browser](docs/browser-assistant.md))
- **Understand it.** Transcription and speaker recognition, text on screen and in scans, faces (off by default),
  entities and topics in a knowledge graph you can explore, query with Cypher or SPARQL, and see as it was at any
  version. Find what is most like a passage, a resource, a topic or an entity, and, with anytopdf, photos by what
  they show. ([Graph](docs/graph.md), [similar content](docs/processing.md#similar-content),
  [graph history](docs/graph-history.md), [topics](docs/topics.md), [video](docs/video.md))
- **Learn from it.** Make a two-host podcast episode about what you pick, every claim cited and checked against its
  source, read aloud in two voices. Keep notes beside every recording, entity and topic, written by you or the
  assistant, and export a resource or a collection as an evidence PDF. ([Podcasts](docs/podcasts.md),
  [notes](docs/notes.md), [evidence PDFs](docs/api.md#evidence-pdfs))
- **Automate it.** Pipelines and workflows on a canvas, routines on a schedule, batch runs over a collection, and
  notifications to webhooks, Slack, Discord and Matterbridge. ([Processing](docs/processing.md#workflows-and-the-canvas),
  [notifications](docs/notifications.md))
- **See what it costs.** An activity ledger of every model call, with reports, charts and budgets that cap spending.
  Setup asks for one overall budget after a cloud model, with a soft limit that warns and a hard limit that stops paid
  calls. A meter in the top bar shows spend against the budget, and the Costs page breaks it down by day, model and
  what cost most. Routine choices go to small decision models, and the LLM only when it adds value.
  ([Activity and costs](docs/activity.md), [budgets](docs/budgets.md#one-budget-for-everything),
  [the meter](docs/budgets.md#the-meter-and-the-costs-page))
- **Keep it up to date and backed up.** Lens checks for a new release once a day and tells admins by email or a
  notification, and upgrades itself through a small agent on the machine, from the API; nothing installs unless an
  admin says so. Settings › Backups takes, lists, downloads and restores backups, takes them on any schedule, and can
  copy each one, encrypted, to a storage connection. ([Updates and backups](docs/updates.md),
  [backups](docs/database.md#backups))
- **Watch it run.** OpenTelemetry traces and metrics, and a Prometheus `/metrics` endpoint. Both are off until you
  turn them on, and send nothing anywhere else. An opt-in `monitoring` profile runs Prometheus, node-exporter and
  Grafana next to Lens, already connected, with the Lens dashboard as Grafana's home. ([Telemetry](docs/telemetry.md),
  [Prometheus](docs/prometheus.md))
- **Stay private and safe.** Passkeys instead of passwords, encryption at rest with a key per namespace, roles per
  namespace, and an audit log. Prompt injection meets several layers: what the assistant reads (recordings,
  documents, web pages, tool results) reaches the model marked as data, not instructions, and text that reads like
  instructions to an AI is flagged (by free patterns, or an optional classifier model that runs on your machine and is
  fetched only when you pick it). Once an answer has read such text, sending anything out, or the browser opening or
  typing what the assistant wrote, waits for your yes. Chat-room answers have links they built themselves broken up,
  agents over MCP get the archive as data, and what Lens caught goes into the audit log. Serve Lens over HTTPS with
  certificates from Let's Encrypt or ZeroSSL that renew on their own, or reach it from anywhere through a Cloudflare
  tunnel with nothing opened on your router. ([Encryption](docs/encryption.md), [access](docs/access.md),
  [prompt injection](docs/assistant.md#third-party-content-and-prompt-injection), [HTTPS](docs/https.md),
  [remote access](docs/remote-access.md))
- **Use open standards.** RDF and Dublin Core for every record, OAuth for apps and OpenID Connect sign-in, MCP, MQTT,
  IIIF for publishing, OpenTelemetry and Prometheus for monitoring, and an optional Fedora mirror.
  ([Linked data](docs/rdf.md), [IIIF](docs/iiif.md), [Fedora](docs/fedora.md))

## Light by default

Lens runs on a small machine. Heavy parts (local speech models, a local LLM, document conversion, Fedora) stay off or
download only when you turn them on, and any of them can be swapped for a cloud provider instead. Every setting lives
in the web app. ([Components](docs/components.md), [speech providers](docs/speech-providers.md),
[local models](docs/local-models.md))

## In progress

These are open pull requests, not yet part of Lens:

- **Chat channels:** a built-in Telegram bot on a shared channel layer
  ([#293](https://github.com/adeelahmad/lense/pull/293)) and a Slack app over Socket Mode
  ([#287](https://github.com/adeelahmad/lense/pull/287)), without Matterbridge; and the assistant's own email address,
  over IMAP and SMTP ([#314](https://github.com/adeelahmad/lense/pull/314)).
- **Assistant safety:** running MCP servers that are programs (stdio) in a sandbox
  ([#304](https://github.com/adeelahmad/lense/pull/304)).
- **Cloud model presets** in Settings and the setup wizard ([#334](https://github.com/adeelahmad/lense/pull/334)).
- **Search:** a Similar tab, and similar content for text selected anywhere
  ([#307](https://github.com/adeelahmad/lense/pull/307)); NSFW pictures classified by a local model, per namespace
  ([#306](https://github.com/adeelahmad/lense/pull/306)).
- **Sources:** watching a local folder in four clicks ([#341](https://github.com/adeelahmad/lense/pull/341)).
- **On a phone:** the web app made usable on a phone ([#346](https://github.com/adeelahmad/lense/pull/346)).
- **Running it:** an upgrade page with live steps that rolls back when the new version doesn't start
  ([#262](https://github.com/adeelahmad/lense/pull/262)), HTTPS set up in Settings
  ([#326](https://github.com/adeelahmad/lense/pull/326)), structured logs with a log viewer and syslog output
  ([#284](https://github.com/adeelahmad/lense/pull/284)), built-in usage analytics
  ([#274](https://github.com/adeelahmad/lense/pull/274)), and turning Prometheus and Grafana on from the web app
  ([#337](https://github.com/adeelahmad/lense/pull/337)).
- **People:** email invitations, and removing someone for good ([#286](https://github.com/adeelahmad/lense/pull/286)).
- **Lens.app for macOS:** a menu-bar app that carries microsandbox
  ([#280](https://github.com/adeelahmad/lense/pull/280)).
- **The graph as files, shared:** read-only over S3 and WebDAV ([#324](https://github.com/adeelahmad/lense/pull/324)),
  SMB ([#305](https://github.com/adeelahmad/lense/pull/305)) and NFS
  ([#295](https://github.com/adeelahmad/lense/pull/295)); assets and people rather than recordings and speakers, unless
  it is audio ([#273](https://github.com/adeelahmad/lense/pull/273)).
- **Reading documents** with an Apache Tika server ([#292](https://github.com/adeelahmad/lense/pull/292)).

## Stack

| | |
|---|---|
| `fastapi_backend/` | FastAPI API (`/api/v1`), MCP server, the processing engine, and the `lens` CLI and workers |
| `nextjs-frontend/` | Next.js web app with a typed client generated from the API's OpenAPI schema |
| `extensions/browser/` | the web clipper browser extension |
| `installer/` | the web installer and the double-click launchers |
| SurrealDB | documents, graph edges, the job queue and graph history; word search in a small SQLite index beside it |

## Documentation

The guides are on the [docs site](https://adeelahmad.github.io/lense/) and in the [docs](docs/) folder:

- **Start:** [Get started](docs/get-started.md), [deployment](docs/deployment.md),
  [microsandbox](docs/microsandbox.md), [configuration](docs/configuration.md), [components](docs/components.md),
  [updates and backups](docs/updates.md), [remote access](docs/remote-access.md), [HTTPS](docs/https.md)
- **Agents and apps:** [MCP server](docs/mcp.md), [API](docs/api.md), [authentication](docs/authentication.md),
  [access](docs/access.md)
- **Use:** [chat and batch runs](docs/assistant.md), [chat rooms](docs/chat-rooms.md), [podcasts](docs/podcasts.md),
  [notes](docs/notes.md), [web clipper](docs/web-clipper.md),
  [the assistant in your browser](docs/browser-assistant.md), [sensors](docs/sensors.md),
  [processing](docs/processing.md), [video](docs/video.md), [notifications](docs/notifications.md)
- **Knowledge:** [graph and Cypher](docs/graph.md), [graph history](docs/graph-history.md), [topics](docs/topics.md),
  [the graph as files](docs/file-tree.md), [linked data (RDF)](docs/rdf.md), [IIIF](docs/iiif.md),
  [Fedora](docs/fedora.md)
- **Models and cost:** [local models](docs/local-models.md), [speech providers](docs/speech-providers.md),
  [activity and costs](docs/activity.md), [budgets](docs/budgets.md)
- **Operate:** [encryption](docs/encryption.md), [storage](docs/storage.md), [database](docs/database.md),
  [telemetry](docs/telemetry.md), [Prometheus](docs/prometheus.md)
- **Build:** [architecture](docs/architecture.md), [web app](docs/frontend.md),
  [technology](docs/technology-selection.md), [API work the design needs](docs/backend-gaps.md),
  [contributing](CONTRIBUTING.md), [agent rules](AGENTS.md)

[Security](SECURITY.md), [threat model](threat-model.md), [changelog](CHANGELOG.md).

## License

Lens is open source under the [MIT License](LICENSE.txt). Optional extras that you install yourself may carry their own
licenses (for example `extract-msg`, GPL-3.0, for Outlook `.msg` files); none is part of the default install.

Built on the [Next.js FastAPI Template](https://github.com/vintasoftware/nextjs-fastapi-template) (MIT).
