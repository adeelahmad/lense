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
[latest release](https://github.com/adeelahmad/lense/releases/latest). On a Mac with Apple silicon,
[Lens.app](docs/get-started.md#the-mac-app) from the same release installs Lens, runs it without Docker and keeps it in
the menu bar (built, early: not signed yet).
[Deployment](docs/deployment.md) covers servers and Cloudron; there are also packages for
[Synology](packaging/synology/README.md), [QNAP](packaging/qnap/README.md) and [Proxmox](proxmox/README.md).
The [checks](docs/requirements.md#install) say which of these CI proves.

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

Everything below is built and merged, and each item links to the checks that prove it: tests, clicks journeys and
measured numbers ([requirements and their checks](docs/requirements.md)). Work that is still in a pull request is
listed under [In progress](#in-progress), and what comes next under [Planned](#planned).

- **Connect your agents.** Lens is an MCP server (`/mcp`): agents search, find assets by face, voice, entity, topic,
  type, content type or date in one call, find what was said about a day, classify an asset or a text with the decision
  model, read assets' text and notes, cite exact moments, find similar content, explore the graph with Cypher or SPARQL
  or browse it as folders and files, suggest topics and graph changes, edit and merge entities and topics (with undo),
  write notes and specs, and make podcasts. They sign in with OAuth and see only what their person may read; the account
  menu copies the MCP address in one click. A namespace can also have an MCP server of its own (`/mcp/<namespace>`) and
  answer OpenAI's chat completions API for any app, cited from that namespace alone, each off until its owner turns it
  on, with API keys that open that one namespace and nothing else. The same folders and files can be mounted over S3,
  WebDAV, NFS or SMB (S3 and WebDAV also at the web app's address, through remote access), so file managers and scripts
  read Lens too, and a file copied in over S3 or WebDAV is imported where it was put. The web app runs on the same REST
  API, so an agent can do what a person can. ([MCP](docs/mcp.md), [one namespace's MCP
  server](docs/mcp.md#one-namespaces-mcp-server), [chat completions](docs/api.md#chat-completions-for-any-app),
  [API](docs/api.md), [the graph as files](docs/file-tree.md), [authentication](docs/authentication.md);
  [checks](docs/requirements.md#connect-your-agents))
- **Ask, and get cited answers.** A chat assistant on every page, and by voice, searches your archive by words (and by
  meaning, once turned on), reads it, cites it and acts on it with tools: changes wait for your approval. Once an admin
  turns them on, it also searches the web and reads pages, cited like the archive, and runs Python and bash in a sandbox
  of each conversation's own. Regenerating an answer or editing a question makes a new version beside the old one, and
  nothing is lost. Each answer keeps a record of what produced it (the model, its tools, how long it took and what it
  cost), opened from its header. It reads answers aloud with a speech provider, macOS `say` or StyleTTS2 on this machine
  (opt-in), any message in one click, and `/clear` and `/compact` keep long conversations short. Each namespace can have its own assistant with
  its own name, instructions and memory, answering in Telegram and Slack (built in, with voice notes and files), in
  Discord, Matrix, WhatsApp and other chat rooms through Matterbridge, and by email at an address of its own that you
  email or forward things to. ([Chat](docs/assistant.md), [web search](docs/assistant.md#web-search-and-reading-pages),
  [code sandbox](docs/assistant.md#code-sandbox), [answer records](docs/assistant.md#what-each-answer-records),
  [chat rooms](docs/chat-rooms.md), [email](docs/assistant-email.md), [speech providers](docs/speech-providers.md);
  [checks](docs/requirements.md#ask-and-get-cited-answers))
- **Extend the assistant.** Add tools (a prompt, a web request, a graph drawn on the canvas, or sandboxed Python),
  skills, hooks and MCP servers, by code, on the canvas or just by asking. Remote MCP servers connect by address, and
  ones that run as a program (`npx`, `uvx`) run in a sandboxed container of their own, off until you turn it on. Add
  one with a form or by pasting another app's config, its tokens sealed and each of its tools switched on or off.
  Install skills and plugins from marketplaces in the open plugin format, or from a repository or an archive.
  ([Extending the assistant](docs/assistant.md#extending-the-assistant-tools-skills-hooks-and-plugins),
  [adding an MCP server](docs/assistant.md#adding-an-mcp-server), [the MCP runner](docs/assistant.md#the-mcp-runner);
  [checks](docs/requirements.md#extend-the-assistant))
- **Bring in everything.** Watched folders on S3, Dropbox, Google Drive, OneDrive, SFTP, SMB, WebDAV or local disks (a
  local folder in four clicks); IMAP mail, Gmail and Google Workspace, iCal calendars, Google Calendar and RSS and Atom
  news feeds; a built-in MQTT broker, syslog and webhook streams; import webhooks for scanners and phone shortcuts.
  Google accounts connect with Connect with Google once Lens has a Google client, and Dropbox, OneDrive and Google
  Drive can sign in through rclone on the Lens server, with no app of your own to register. Upload audio, video, PDFs,
  Office files, photos, JSON and zip or tar archives, and, with an Apache Tika server (an opt-in extra), documents
  nothing else here can read; capture web pages one at a time, from a link list or a bookmarks file, by API or by asking
  the assistant, from private networks you allow in Settings too; and import videos and podcasts by their address (off
  by default).
  ([Sensors](docs/sensors.md), [processing](docs/processing.md), [Apache Tika](docs/configuration.md#apache-tika),
  [import webhooks](docs/api.md#import-webhooks); [checks](docs/requirements.md#bring-in-everything))
- **Clip from the browser.** The Lens web clipper for Chrome, Edge, Firefox and Safari turns a page, a selection or a
  part you pick into Markdown, lets you look it over, and sends it to a namespace. New releases reach installed copies
  through the browser's store or a self-hosted update file. In Chrome and Edge, its side panel is a chat with the
  assistant, which can also use that browser on the sites you allow (open, read, click, type), asking before anything
  that sends, deletes, buys or posts. ([Web clipper](docs/web-clipper.md),
  [the assistant in your browser](docs/browser-assistant.md); [checks](docs/requirements.md#clip-from-the-browser))
- **Understand it.** Transcription, people recognised by voice (long recordings go to a speech provider in pieces that
  fit its limits), text on screen and in scans, faces (off by default), NSFW pictures flagged by a model on your own
  server (off by default, per namespace), entities and topics in a knowledge graph you can explore, query with Cypher or
  SPARQL, and see as it was at any version. Every entity has its own page at a wiki address
  (`/wiki/<namespace>/<Name>`), and entities, topics and people are edited and merged from their lists, the graph or an
  asset, with Undo instead of a confirm. Names get their real types (people, places, organisations, events, works) by
  rules, without a model. Find what is most like a passage, an asset, a topic or an entity, in a Similar
  tab on every asset or by selecting text anywhere, and, with anytopdf, photos by what they show. Pages and shots are
  described in words by the model provider's vision model or, off by default, by Florence-2 on your own server.
  Transcripts can be cleaned up before analysis (stutters, "um"s, spacing and, with a local model, punctuation put
  back), entities found by spaCy instead of the rules, and the dates people say read as days on the calendar, each off
  by default.
  ([Graph](docs/graph.md), [entity pages](docs/notes.md#entity-pages),
  [similar content](docs/processing.md#similar-content), [graph history](docs/graph-history.md),
  [topics](docs/topics.md), [video](docs/video.md), [NSFW classification](docs/nsfw.md),
  [long recordings](docs/speech-providers.md#long-recordings),
  [cleaning up transcripts](docs/processing.md#cleaning-up-transcripts), [dates](docs/processing.md#dates);
  [checks](docs/requirements.md#understand-it))
- **Learn from it.** Make a two-host podcast episode about what you pick, every claim cited and checked against its
  source, read aloud in two voices. Turn a voice session or a meeting into a technical spec, in a note linked to it,
  that keeps every decision, every no and every constraint in the speaker's own words, linked to when they said them,
  flags what was left to decide later and marks any quote the transcript doesn't hold. Keep notes beside every asset,
  entity and topic, written by you or the assistant, in a tree where notes are renamed, moved, copied and pinned (pins
  are your own) and each note shows the notes it's inside and the ones inside it, with today's note one click away,
  templates to start from, a search by their words, a download as Markdown, and Undo and Recently deleted instead of a
  confirm. Export an asset or a collection as an evidence PDF with everything Lens found about it, or the parts you
  pick. ([Podcasts](docs/podcasts.md), [specs from transcripts](docs/notes.md#specs-from-transcripts),
  [notes](docs/notes.md), [evidence PDFs](docs/api.md#evidence-pdfs); [checks](docs/requirements.md#learn-from-it))
- **Automate it.** Pipelines and workflows on a canvas, routines on a schedule, batch runs over a collection, and
  notifications to webhooks, Slack, Discord and Matterbridge. Everything that happens in Lens (new assets, emails,
  calendar events, files, what people say to Lens, sensor readings, notes, graph changes, runs, budget warnings) is kept
  as a stream of activities in W3C Activity Streams form, which agents read through the API, MCP and the assistant, and
  people review on the Timeline page (and Latest on Home), marking what they've seen. Automations act on that stream, on
  a schedule, at a time or when their own webhook is called: they test conditions, then tell you in your own chat app,
  ask the model or the agent, use a tool, call a web address (its credentials kept sealed), run a workflow, pipeline or
  routine, or change assets and notes. A Classify node in a workflow sorts an asset into labels, content types or
  topics, acting only on a sure answer. Anything that reaches outside Lens or changes more than what set it off asks
  first, until you let it run on its own: on the Approvals page or, for a change inside Lens, with a yes in the chat it
  told you in; agents can decline an approval but never give one. Each automation has limits and budgets, and agents
  make, try and run them over MCP and in the assistant, or draft one from a sentence; the studio to draw them in the web
  app is planned. ([Processing](docs/processing.md#workflows-and-the-canvas), [notifications](docs/notifications.md),
  [automations](docs/automations.md), [approvals](docs/automations.md#approvals), [timeline](docs/timeline.md);
  [checks](docs/requirements.md#automate-it))
- **See what it costs.** An activity ledger of every model call, with reports, charts and budgets that cap spending.
  Setup asks for one overall budget after a cloud model, with a soft limit that warns and a hard limit that stops paid
  calls. A meter in the top bar shows spend against the budget, and the Costs page breaks it down by day, model and
  what cost most. Routine choices go to small decision models, and the LLM only when it adds value.
  ([Activity and costs](docs/activity.md), [budgets](docs/budgets.md#one-budget-for-everything),
  [the meter](docs/budgets.md#the-meter-and-the-costs-page); [checks](docs/requirements.md#see-what-it-costs))
- **Keep it up to date and backed up.** Lens checks for a new release once a day and tells admins by email or a
  notification. One click in Settings › Updates (or one API call) upgrades it through a small agent on the machine,
  showing each step, and rolls back when the new version doesn't start; nothing installs unless an admin says so. The
  same agent changes whether other devices reach Lens and which edition runs, from Settings. Settings › Backups takes,
  lists, downloads and restores backups of the database and Lens's files (a file that hasn't changed takes no new room),
  takes them on any schedule, and can copy each one, encrypted, to a storage connection. Before a version is released,
  CI upgrades the one before it, holding data, to it, and releases nothing when any of that data is lost.
  ([Updates and backups](docs/updates.md), [backups](docs/database.md#backups),
  [the upgrade check](docs/database.md#checked-before-every-release);
  [checks](docs/requirements.md#keep-it-up-to-date-and-backed-up))
- **Watch it run.** Structured logs from every process, read in Settings › Logs and optionally sent to a syslog
  server. OpenTelemetry traces and metrics, and a Prometheus `/metrics` endpoint, both off until you turn them on, and
  sending nothing anywhere else. On a Docker install, one switch in the web app (or one API call from an agent) starts
  Prometheus, node-exporter and Grafana next to Lens, already connected, with the Lens dashboard as Grafana's home.
  The Analytics page shows admins active people, churn, retention and which features get used, worked out on this
  machine; the same daily events can also go to a Google Analytics 4 property of your own (off unless you turn it on).
  ([Logging](docs/logging.md), [telemetry](docs/telemetry.md), [Prometheus](docs/prometheus.md),
  [usage analytics](docs/analytics.md); [checks](docs/requirements.md#watch-it-run))
- **Stay private and safe.** Passkeys instead of passwords, encryption at rest with a key per namespace, roles per
  namespace (viewer, editor, owner, or custom roles made of permissions), email invitations, removing someone for
  good, and an audit log. Prompt injection meets several layers: what the assistant reads (recordings,
  documents, web pages, tool results) reaches the model marked as data, not instructions, and text that reads like
  instructions to an AI is flagged (by free patterns, or an optional classifier model that runs on your machine and is
  fetched only when you pick it). Once an answer has read such text, sending anything out, or the browser opening or
  typing what the assistant wrote, waits for your yes. Chat-room answers have links they built themselves broken up,
  agents over MCP get the archive as data, and what Lens caught goes into the audit log. Serve Lens over HTTPS, set
  up in Settings, with certificates from Let's Encrypt or ZeroSSL that renew on their own, answering only for its own
  domains and, if you choose, only to devices holding a client certificate; or reach it from anywhere through a
  Cloudflare tunnel with nothing opened on your router. ([Encryption](docs/encryption.md), [access](docs/access.md),
  [prompt injection](docs/assistant.md#third-party-content-and-prompt-injection), [HTTPS](docs/https.md),
  [remote access](docs/remote-access.md); [checks](docs/requirements.md#stay-private-and-safe))
- **Use open standards.** RDF and Dublin Core for every record, OAuth for apps and OpenID Connect sign-in, MCP, MQTT,
  IIIF for publishing, OpenTelemetry and Prometheus for monitoring, and an optional Fedora mirror.
  ([Linked data](docs/rdf.md), [IIIF](docs/iiif.md), [Fedora](docs/fedora.md);
  [checks](docs/requirements.md#use-open-standards))

## Light by default

Lens runs on a small machine. Heavy parts (local speech models, a local LLM, document conversion, an Apache Tika
server, the NSFW model, the MCP runner, Fedora) stay off or download only when you turn them on, and any of them can be
swapped for a cloud provider instead. Setup asks only what has no default: the model, and a budget for a cloud model.
Picking a cloud model provider fills in its address and a model, so only its key is left to paste. Every setting lives
in the web app, which works on a phone too: settings are in named groups, a search finds any one by name, and the
options most people leave as they are stay folded. ([Components](docs/components.md),
[speech providers](docs/speech-providers.md), [local models](docs/local-models.md),
[cloud model providers](docs/configuration.md#cloud-model-providers); [checks](docs/requirements.md#light-by-default))

## In progress

These are open pull requests, not yet part of Lens:

- **The assistant** answering questions about Lens itself, from its docs and this server
  ([#400](https://github.com/adeelahmad/lense/pull/400)).
- **Chat:** a conversation exported as Markdown, PDF or JSON ([#474](https://github.com/adeelahmad/lense/pull/474)).
- **Pictures:** low-quality images enhanced before they're read ([#479](https://github.com/adeelahmad/lense/pull/479)).
- **Web clipper:** signing in, or using your API token, not only an import webhook's
  ([#406](https://github.com/adeelahmad/lense/pull/406)).
- **Automations:** more operators for conditions ([#434](https://github.com/adeelahmad/lense/pull/434)).

## Planned

Next, and not in a pull request yet; each links to where it is tracked:

- **The automations studio:** drawing automations in the web app, joins, and the decide node, where a decision model
  picks the branch ([automations](docs/automations.md)).
- **Timeline:** what each item caused, and Approve and Decline on waiting approvals ([timeline](docs/timeline.md)).
- **Notes:** nesting a note under its project or area without a click, pages in the graph, and wiki addresses for
  topics, recordings and the other things that have pages ([notes](docs/notes.md)).
- **Local models:** GPU builds of llama.cpp fetched by Lens, and local embedding models
  ([local models](docs/local-models.md)).
- **Speech providers:** their summaries, chapters and entities as optional imports, and per-minute prices in the
  activity ledger ([speech providers](docs/speech-providers.md)).
- **Storage:** uploads and assets' media in the file store, and a storage key to download
  ([storage](docs/storage.md)).
- **Logging:** sending straight to Loki or another HTTP endpoint, and client certificates for TLS
  ([logging](docs/logging.md)).
- **Proof:** each release tested by upgrading an install of the last one, holding data
  ([#482](https://github.com/adeelahmad/lense/issues/482)), and the scorecard's install-to-first-answer and Raspberry Pi
  numbers, not measured yet ([#481](https://github.com/adeelahmad/lense/issues/481)).

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
- **Use:** [chat and batch runs](docs/assistant.md), [chat rooms](docs/chat-rooms.md),
  [the assistant by email](docs/assistant-email.md), [podcasts](docs/podcasts.md), [notes](docs/notes.md),
  [web clipper](docs/web-clipper.md), [the assistant in your browser](docs/browser-assistant.md),
  [sensors](docs/sensors.md), [processing](docs/processing.md), [video](docs/video.md),
  [NSFW classification](docs/nsfw.md), [notifications](docs/notifications.md), [automations](docs/automations.md),
  [timeline](docs/timeline.md)
- **Knowledge:** [graph and Cypher](docs/graph.md), [graph history](docs/graph-history.md), [topics](docs/topics.md),
  [the graph as files](docs/file-tree.md), [NFS share](docs/file-shares.md), [SMB share](docs/smb.md),
  [linked data (RDF)](docs/rdf.md), [IIIF](docs/iiif.md), [Fedora](docs/fedora.md)
- **Models and cost:** [local models](docs/local-models.md), [speech providers](docs/speech-providers.md),
  [activity and costs](docs/activity.md), [budgets](docs/budgets.md)
- **Operate:** [encryption](docs/encryption.md), [storage](docs/storage.md), [database](docs/database.md),
  [logging](docs/logging.md), [telemetry](docs/telemetry.md), [Prometheus](docs/prometheus.md),
  [usage analytics](docs/analytics.md)
- **Build:** [architecture](docs/architecture.md), [web app](docs/frontend.md),
  [technology](docs/technology-selection.md), [API work the design needs](docs/backend-gaps.md),
  [contributing](CONTRIBUTING.md), [agent rules](AGENTS.md)

[Security](SECURITY.md), [threat model](threat-model.md), [changelog](CHANGELOG.md).

## License

Lens is open source under the [MIT License](LICENSE.txt). Optional extras that you install yourself may carry their own
licenses (for example `extract-msg`, GPL-3.0, for Outlook `.msg` files); none is part of the default install.

Built on the [Next.js FastAPI Template](https://github.com/vintasoftware/nextjs-fastapi-template) (MIT).
