# Chat, collections and batch runs

## Chat

Conversations belong to one person and can be scoped to namespaces, assets, collections, people and dates. A
collection in the scope is read each time the assistant answers, so it draws on the collection's assets as they are
then.

- **Retrieval:** the question's keywords go through the word index (English stemming) and, when search by meaning is
  set up, passages about the question in other words are found too
  ([Search by meaning](processing.md#search-by-meaning)); the two are ranked together by reciprocal rank, limited to
  namespaces the person can read and to the conversation's scope. Hits are widened to their neighbouring lines and
  numbered.
- **Answers:** the model answers only from those excerpts and cites them as [n]. Each citation carries the asset
  and timestamp.
- **Streaming:** answers arrive over server-sent events: `passages`, then `token`s, then `meta` (the answer's
  record, [below](#what-each-answer-records)) and `done`.
- **Stopping:** Stop asks the server to end the answer after the piece or tool step it's on (whichever server process
  is writing it); what came before is saved, marked stopped. A model call already under way finishes first.
- **Reopening:** a conversation's answers keep the tools the assistant used (and with what), any notice (the model
  couldn't use tools), the error when there was no answer, their latest source check and what produced them.
- **Versions:** a conversation is a tree, and nothing in it is deleted. Regenerating an answer writes a new answer to
  the same question beside it, with the same model or another (`POST /api/v1/chats/<id>/messages` with `regenerate`:
  the answer's id, and `model`); editing a question makes a new version of it beside the old one, which keeps what
  followed it (`edit`: the question's id, with the new `content`). The version showing is what the model reads and
  what the next question goes on from; `parent` goes on from any other message instead. `GET /api/v1/chats/<id>`
  gives the version showing as `messages`, each with its `versions` (`index` from 1, `count`, `ids`: it and the
  others in its place, oldest first), every version's messages as `tree`, and `head`, the last message showing. `PUT
  /api/v1/chats/<id>/head` with `{"message": <id>}` shows the version that message is in, to its newest message. A
  new version streams `branch` first (`message`, `parent`, `versions`). Chat rooms ([Chat rooms](chat-rooms.md)) and
  automations go on from the version showing. In the web app, Edit makes a version; switching between versions and
  Regenerate are in progress.
- **Long conversations:** the model reads the latest six messages word for word. Once eight more have built up
  behind them, they're folded into a running summary kept on the conversation (one model call, after an answer), and
  every answer gets that summary plus the messages it doesn't cover yet. Room conversations (chat-rooms.md) go on for
  good, so they rely on it most. A summary covers the version it was written on: on a version that leaves what it
  covers, the model reads that version's messages, and the next summary is made from them. A conversation that fills half of the model's context window (**Chat model's context**, `ai.context_tokens`,
  16000 when empty; about four characters a token) is folded in early, before the next answer, whatever the count.
  **Model for summaries** (`ai.compact_model`) writes them with a smaller, cheaper model on the same provider; empty,
  the chat's model does. Settings › AI assistant › Summarise long conversations (`ai.compact`, on) turns it off; off,
  or with no model, only the last six messages are read.
- **/clear and /compact:** typed as a message, in the app or in a chat room (`Lens /clear`). `/clear` makes the
  assistant start afresh: the messages stay to read, but the model no longer sees them or their summary.
  `/compact` folds the whole conversation into its summary now, and `/compact keep the dates and owners` tells the
  summary what to keep; the reply shows the summary. Both work with automatic summaries off. Neither command, nor its
  reply, is read by the model afterwards.
- **Changed access:** old citations are filtered by the person's current access when a conversation is reopened.
- **Choosing the model:** a conversation can use any model an admin offers (`llm.chat_models`, else whatever the model
  server lists); Try another model asks a question again with a different one. Each answer records the model that
  wrote it.
- **Chat on any page:** every page except Chat itself has an Ask button that opens a chat panel beside the page (full
  screen on phones). Its conversation follows you between pages, and across visits in the same browser, until New
  chat; it is an ordinary conversation, also listed in Chat. Each question can share the page's text (the page chip,
  on by default) and text highlighted on the page (Ask about this, above any selection). The model reads them with the
  question (up to 12,000 characters of page text and 4,000 of the highlight) as what the person is looking at, not as
  citable excerpts. The question keeps the page's address, title and highlight, shown above it; the page's text isn't
  stored. An asset's text and pages keep their own selection toolbar, whose Ask in chat goes to the
  asset's chat. The Ask button stays clear of the page's own buttons: when one is under it and scrolling can't
  move it away (a row of buttons pinned to the bottom of the screen, or the last button with the page at its end), it
  rises above them, or steps out of sight when there's no room to rise.
- **No model configured:** chat returns the best-matching passages instead. Anyone signed in can see whether a model
  is set up, and which (`GET /chats/capabilities`), so the app says so before the first question.

### What each answer records

Every answer keeps a record of what produced it (`meta` on the message), so a person can trust and debug it and an
agent can read it. The **i** in an answer's header (Details) shows it, with the calls made for it and what the
conversation has cost this month; on a phone it opens as a sheet.

| Field | What it says |
| --- | --- |
| `model`, `provider` | the model that wrote it, and the host name of the model server it ran on (never the server's address, path or key) |
| `tools` | `offered`: every tool the model was offered; `picked`: the tools the question asked for (null: all it may use) |
| `scope` | the scope it answered in, after a conversation over everything was narrowed to a namespace |
| `limits`, `used` | the rounds of tool calls it had (and the tool calls, when they're limited), and how many it used |
| `outcome` | `answered` (after looking things up), `direct` (without looking anything up), `limit` (its rounds ran out, so it answered from what it found), `fallback` (from a search: the model can't use tools, or none is set up), `stopped` or `error` |
| `tokens`, `cost_usd`, `cost_estimated` | the tokens of the model calls made for it, and what they cost from the prices in Settings ([Costs](activity.md#costs)); `cost_estimated` when some had a price but no token counts, so it cost at least this |
| `ms` | how long it took, from the question |
| `flagged` | tools whose results read like instructions to an AI ([below](#third-party-content-and-prompt-injection)) |
| `version` | the version of Lens that wrote it |

Each tool step also keeps how long it took (`ms`) and whether it failed (`failed`). A question keeps how it was sent
(`via`: `web`, `api`, `voice`, `room` or `automation`) and the options it was asked with (`options`, such as another
`model`).

An answer is written inside the activity ledger's scope `chat_message:<id>` (with `chat:<id>`), so every model and tool
call made for it names it ([Activity](activity.md)), and its record's tokens and cost are theirs: one ledger total per
answer, and no model call. `GET /api/v1/chats/<id>` returns the record on each message, and `GET
/api/v1/chats/<id>/messages/<mid>` returns one message, of any version, with its record and those ledger rows
(`ledger`). Answers in chat rooms ([Chat rooms](chat-rooms.md)) and the automations' agent record theirs the same way.
An app's chat completions keep no messages, so their record comes back in the reply's `lens.record`, without tokens and
cost ([Chat completions](api.md#chat-completions-for-any-app)). Answers from before Lens kept records have none; Details
shows what they have and says the rest wasn't recorded.

### Picking the namespace for a conversation over everything

When a conversation has no scope (the assistant home starts it over everything), its first question goes to the
decision model (`decide.choose`, see the decisions settings) with the namespaces the person can read, a line about each
(its description and recent titles) and how many matching excerpts each holds. A choice at or above
`decisions.act_above` narrows the conversation to that namespace before it answers: the stream starts with a `scoped`
event and the answer records a `choose_namespace` step. When it isn't sure, a `suggested` event offers the likeliest
namespaces; when "none of these" wins, admins are also offered a couple of new names from the language model. The app
shows them as chips above the composer (in voice mode too, where saying a name picks it); a new one is created only
when picked, and nothing changes if none is. A scope the person set, setup conversations and later questions are
never narrowed this way.

### Assistant mode and voice

Home opens in **assistant mode** (one field and a big mic, like a search page) when the archive has any content, and on
the overview when it's empty; the switch at the top right remembers the person's pick in the browser. Enter in the field
opens a new conversation over everything the person can read and sends what was typed as its first question
(`/chat?global=1&q=…&send=1`); the mic opens one in voice mode (`/chat?global=1&voice=1`). Voice mode listens, sends
what was heard, reads the answer aloud (without citation marks) and listens again, until the mic is tapped off or
nothing is said twice in a row. The chat composer's mic turns it on in any conversation.

Voice goes through one hook, `useVoice()` in `nextjs-frontend/lib/voice.ts` (`listen`, `speak`, `stop`).

- **Hearing.** When the server has a speech-to-text engine (SenseVoice or Whisper, which it fetches for itself, see
  [Components](components.md)), the browser records the mic and the server turns it into text with that engine:
  speech stays on the server, and it works in every browser that can record (Firefox too). What's been heard so far
  shows in the field while talking; a pause of about a second ends the turn, and nothing said for seven seconds ends
  listening. Without a server engine, or with `voice.input: browser`, the browser's own recognition listens (Chrome,
  Edge and Safari; Chrome sends the audio to its own speech service).
- **Speaking.** Answers are read by a speech model when one is set (`voice.tts_model`: any OpenAI-compatible
  `/audio/speech`, such as Kokoro-FastAPI, LocalAI or OpenAI's `tts-1`, on `voice.tts_base_url` or the LLM provider),
  or by ElevenLabs, Deepgram, Google Gemini or macOS `say` ([Speech providers](speech-providers.md#voices)); else by
  the browser.

Settings → AI assistant → Voice holds these. The API: `GET /voice` (what the server does; it also starts loading the
engine), `POST /voice/transcribe` (a clip as the raw body; nothing is kept), `POST /voice/speak` (`{text, voice?}`;
204 when the browser should read it), `GET /voice/voices` (the voices a provider reads in).

## The assistant's tools

When the configured model supports function calling, chat becomes an agent.

- **What it can look things up with:** assets' text (transcripts and documents, and on-screen text), assets, passages,
  outputs, entities and their mentions and timelines, the knowledge graph, people, and content similar to a text,
  an asset, a topic or an entity (`find_similar`), and what happened in Lens, newest first (`timeline`, the
  [stream of activities](automations.md#timeline-reads); `unreviewed` for what the person hasn't marked reviewed).
- **The person's review marks:** `mark_activities` marks activities of the timeline reviewed, important or hidden for
  the person ([Timeline](timeline.md#review-marks)). After it has read text from outside Lens, hiding or marking
  reviewed waits for their yes; marking important or bringing things back acts at once.
- **The person's approvals:** `approvals` lists what waits for their pick ([Approvals](automations.md#approvals)):
  automations' actions about the namespaces the conversation reads, and its own proposals in this conversation.
  `decline_approval` declines one; after it has read text from outside Lens (the approvals' own summaries count),
  declining waits for the person's yes. It can't approve: that is the person's pick. An automation's agent doesn't
  get `decline_approval`.
- **Automations:** `automations` lists the person's automations, or one in full with its latest runs, or one run;
  `test_automation` tries a saved one or a draft on an activity or a kind's sample, keeping nothing. People who edit a
  namespace also get `save_automation`, which saves one at once, switched off, and `run_automation`. Switching one on
  (`save_automation` with `enabled`) and running one now wait for the person's approval; switching one off acts at once
  ([Automations](automations.md#agents)).
- **Limits:** everything is limited to what the person can read and to the conversation's scope, and capped by
  `ai.max_steps` (Settings → AI assistant, Tool steps per answer; 6 by default) and `ai.max_transcript_reads`.
- **Always an answer:** the model is told its step budget. When the steps run out, or it replies with nothing, it is
  asked once more to answer from what it found, with the tools off, and to say what it didn't get to check. A server
  that calls tools anyway gets the conversation again as plain text, without tools. That last ask is one more model
  call, logged in the activity ledger like the others. The same archive read twice in one answer (same tool, same
  arguments) isn't run again: the model is pointed at the result it already has.
- **Citations:** every moment a tool returns is numbered, so answers cite [n] across everything the assistant read.
- **Visible steps:** each tool call streams as a step, for example 'Searched for "refund": 42 matches'.
- **Approvals:** running a template on assets, changing entities (merging, renaming, retyping, describing one
  and its other names, hiding one, or adding one to a namespace's fixed list) and changing topics (merging,
  renaming, describing or deleting one; [Topics](topics.md)) don't happen directly.
  They become approval cards (with the batch estimate) that the person approves, approves on a sample, or declines
  (`POST /api/v1/approvals/<id>`). Viewers only get the read tools. When an [automation](automations.md#the-model-and-the-agent)
  uses the assistant, every change it would make is an approval of yours, notes included, with the run as its origin.
- **Check sources** (`POST /api/v1/chats/<id>/messages/<id>/check`) re-checks every cited claim against the lines it
  cites, and lists sentences that cite nothing.
- **Fallback:** a model that can't call tools falls back to search-and-answer, with a notice. A model that answers
  straight away without looking anything up gets the same, when the archive has passages that match.
- **Files:** attach files to a message with the clip, or by dropping or pasting them on the box. They upload as you
  type (`POST /api/v1/uploads` with `hold`), stay out of the archive, and the assistant puts them in a namespace
  with `import_files` (`attachments` on `POST /api/v1/chats/<id>/messages`).
- **Web pages:** give it one address, several, or a list of links (pasted, or attached as a text file or a browser's
  bookmark export) and `import_urls` captures each page as a document, as `POST /api/v1/import/web/links` does. Like
  `import_files`, it picks the namespace when you don't name one, and waits for your approval.
- **Keep typing:** what you send while an answer is being written waits its turn and goes next.
- **Your browser** (Chrome and Edge): asked from the Lens extension's side panel, the assistant can open, read, click
  and type in that browser and download files into the conversation, on sites you allow, asking before anything that
  sends, deletes or buys. See [The assistant in your browser](browser-assistant.md).

## Third-party content and prompt injection

The assistant reads text nobody vouched for: what was said in recordings, documents, text read off screens, web pages
and emails that were captured, chat-room messages, notes and memories kept earlier, and what tools, extensions and other
servers send back. Any of it can carry instructions meant for the model ("ignore the above and..."), so Lens keeps it
apart from what the person asks:

- **Marked as data** (`ai.untrusted_marking`, on by default; Settings → AI assistant → Treat what the assistant
  reads as data, not instructions): each excerpt, each tool result, the page a question was asked from and the namespace
  assistant's memories reach the model between markers that say where they came from and share a random code, and
  the model is told to read, quote and cite marked text but never follow instructions in it, nor put what it read
  into web addresses or tool arguments unasked. Results that are Lens's own (server status, settings, the skills you
  chose, what a change tool reports) aren't marked. Turn it off only for a model the markers confuse.
- **Control tokens broken up:** chat templates' control tokens in that text (`<|im_start|>`, `[INST]`,
  `<start_of_turn>`, `<tool_call>` and the like) and anything that looks like a marker get a space inside, so a local
  model server that reads them in a message can't be made to end the turn and speak as the system. Unicode's
  invisible tag characters, which can spell out instructions nobody sees on screen, are taken out.
- **Flagged when it reads like instructions** (`ai.injection_check`; Settings → AI assistant → Flag text that reads
  like instructions to an AI): text that says "ignore previous instructions", claims to be new instructions or a new
  role, addresses an AI reading it, asks to hide something from you, to reveal the system prompt, to call a tool or
  to put data into a link, or carries control tokens or hidden characters, gets a warning on its marker for the
  model, and the step in the answer says so ("Read 12 line(s) of Mallory's talk (reads like instructions to an AI:
  ignore previous instructions)"). `patterns` (the default) is free and kept narrow, so it misses rephrased
  attacks. `classifier` also runs each tool result the patterns let through past a small model trained to spot
  prompt injection (ProtectAI's deberta-v3-base-prompt-injection-v2, Apache-2.0) on this machine: nothing leaves it
  and no tokens are paid for, but the model is about 740 MB, fetched when you pick it (Settings → Components shows
  it), takes a moment per result (seconds on a Raspberry Pi), reads English best, and flags some requests written for
  people too (an email asking to add a tracking number to a link); until it is here the patterns carry on alone.
  `model` asks the decision model instead
  (one routine call per tool result, falling back to the chat model when no decision model is set up). Each check is
  a row in the activity ledger. `off` turns flagging off. A flag is a warning, not a block: the approvals below are
  what stop an action.
- **Asks first once it has read it** (`ai.untrusted_asks`, on by default; Settings → AI assistant → Ask before
  sending data out after reading third-party text): once an answer has read text from outside Lens (a tool's result
  other than Lens's own, the page it was asked from, the namespace assistant's memories, a message in a chat room
  open to anyone, or an earlier answer in the conversation that read any of these), anything that could carry what
  it read out of Lens waits for your approval, even where it would otherwise run at once: your own `read` web tools,
  other servers' MCP tools and Python tools with the network, the changes a setup conversation makes itself, and, in
  [your browser](browser-assistant.md), opening or downloading an address or typing text you didn't write. The
  approval card says why ("asked first: this answer read text from outside Lens: tool search_transcripts"). Notes and
  memories are still written at once: they stay in Lens, and are marked as data when read back.
- **Links a chat app would fetch broken up:** in chat rooms, a web address the answer built itself can't be previewed
  ([chat rooms](chat-rooms.md)); the web app shows no links or images that load by themselves; MCP results reach
  other agents with control tokens broken up and a note when flagged ([MCP](mcp.md)).
- **Logged:** each flagged tool result (`assistant.injection_flagged`, with the tool and the reasons, never the text)
  goes into the audit log, which admins read (`GET /api/v1/audit`).

The layers stand on their own: the person's reads bound what any answer can see whatever the model does, marking and
flags work without approvals, approvals don't depend on anything being flagged, and links are broken up whether or
not the answer asked first. Turning one off leaves the others. Marking and flagging make injection less likely to
work; they don't make it impossible (a system prompt is not a filter). What the assistant can do is still bounded by
the person's reads and approvals (N-PROMPT in the repository's `threat-model.md`).

## A namespace's own assistant

Each namespace can have its own assistant that remembers from one conversation to the next. It is off until an owner
of the namespace turns it on (`PATCH /api/v1/namespaces/<name>/assistant` with `enabled`, `name` and `instructions`).
Then every conversation scoped to that namespace alone, picked by hand or chosen from the first question, uses it.
It is the same assistant with the same tools, extensions and approvals; what it adds:

- **A name and instructions** from the namespace's owners, read with every question.
- **A memory:** short facts it keeps with `remember` when it learns something lasting (a decision, a preference, a
  plan), each with where it came from: the exact moment of the excerpt it was shown, or the conversation it was told
  in. Remembering is routine, so it happens at once and shows as a step; people who can edit the namespace can also
  write memories themselves. Viewers' conversations read the memory but don't add to it.
- **Cited:** the newest 30 memories (pinned ones first) are read with every question. A memory from a moment is
  numbered like an excerpt, so an answer from memory cites [n] and opens the asset at that moment; older ones are
  found with `recall`. A memory from an asset the asker can't read is left out.
- **Asks first:** forgetting from chat waits for the person's approval, like every tool that changes data.
- **Yours to edit:** `GET /api/v1/namespaces/<name>/assistant/memories` (`q` searches them), `POST` to add one,
  `PATCH .../memories/<id>` to correct or pin one, `DELETE .../memories/<id>` to forget it, and owners `DELETE
  .../memories` to forget everything.

Owners find it on the namespace's page (Admin → Namespaces → the namespace → Assistant): the switch, its name and
instructions, and what it remembers, with where each memory came from, to add to, pin or forget.

It makes no model calls of its own and keeps no index, so it costs nothing extra on a Raspberry Pi.

A chat room can be given to a namespace's assistant too ([chat rooms](chat-rooms.md)).

Refine later: a decision model choosing what's worth remembering, similar memories merged.

## Extending the assistant: tools, skills, hooks and plugins

Anyone who can edit can add to what the assistant does. Each addition is an *extension*: versioned, kept private or
shared with namespaces (admins can share with everyone), and switched on or off in one place (`/api/v1/extensions`).

- **Tool**: something the assistant can call, with typed parameters. Its body is a prompt (`{{param}}` in the text), a
  web request (public addresses only), or a graph drawn on the canvas (`ask_model`, `call_tool` and the primitives,
  from `arg` nodes to `return` nodes), or Python code (admins only, below). A tool with `effect: change` asks for
  approval before it runs, and so does another person's tool (unless an admin shared it), since `effect: read` is
  only its author's word.
- **Skill**: instructions and a line saying when to use them. The assistant sees the line, and reads the instructions
  only when the skill applies.
- **Hook**: runs when a question arrives, before or after a tool, or after an answer, to add context, block a tool or
  call one. Your own hooks run in your conversations; other people's run only when an admin shared them.
- **MCP server** (admins add them): a server that speaks the Model Context Protocol over Streamable HTTP, at the
  address an admin gives (this machine and the local network included, as for the model's address), with the headers
  it needs (a token). Lens lists its tools when it's saved, and the assistant calls them as `<server>__<tool>`: one the
  server marks read-only answers at once, the others ask for approval. A server that runs as a program (stdio:
  `command`, `args` and `env` instead of an address, as in Claude's `.mcp.json`, usually `npx` or `uvx`) runs in the
  MCP runner, below.
- **Plugin**: a bundle of tools, skills, hooks and MCP servers, shared and switched off as one.

Make one however suits you:

- **Code**: a manifest, Markdown with YAML frontmatter (the body is a skill's instructions or a prompt tool's prompt),
  or YAML. `POST /api/v1/extensions/check` checks it without saving, `GET /api/v1/extensions/{id}` gives it back.
- **Canvas**: a tool whose `run` is `{type: graph, graph}`.
- **Python** (admins only): a tool whose `run` is `{type: python, code, seconds, network}`, where `code` defines
  `run(**args)` and returns something JSON can hold. It runs in a Python process of its own, from an empty folder it
  alone reads and writes, with none of the server's environment (no keys or database address), the standard library
  only, limits on CPU time (`seconds`, up to 60), memory and files, and no web unless `network: true` (a network
  namespace of its own where the machine allows one, and a guard on sockets either way). What it prints comes back
  next to its result. It stops running if its owner stops being an admin. The guard raises the bar, it isn't a wall,
  which is why only admins write these.
- **Chat or voice**: ask the assistant ("make me a tool that translates text into French"). It drafts the manifest,
  fixes what the check says, and asks for your approval before saving, changing or switching anything.

```markdown
---
name: meeting_recap
kind: skill
description: Recaps a meeting
when: someone asks for a recap of a meeting
---

Find the meeting, read it, and answer with three bullets: decisions, owners, dates.
```

### Skills and plugins from marketplaces

Lens reads Anthropic's open plugin and skill formats as they are, so what's made for Claude installs here unchanged:

- **Marketplaces** (`/api/v1/marketplaces`): a repository with `.claude-plugin/marketplace.json`. Admins add them,
  which is the approval: Anthropic's own (`anthropics/skills`, `anthropics/claude-plugins-official`) are suggested,
  and any other is added by its address (`owner/repo`, a GitHub or GitLab link, or a link to its marketplace.json),
  which is how a team runs a custom one. Everyone who can edit browses them, previews a plugin (what it adds and what
  Lens leaves out) and installs it.
- **Links and files** (`POST /api/v1/extensions/import`, `/import/archive`): a plugin or a skill from a GitHub or
  GitLab repository or folder, or a `.zip`, `.skill` or `.tar.gz`.

An installed plugin is one Lens plugin, yours and private until you share it; installing it again saves its new
version. A skill (a folder with SKILL.md) is a skill, and the assistant reads its text files (references, examples,
scripts) when the instructions point at them; nothing in them runs. A command (`commands/*.md`) is a skill the
assistant follows when someone asks for it, and an agent (`agents/*.md`) is a prompt tool with the agent's instructions
as its system prompt. Claude Code's hooks run commands on the computer, so they're left out. A plugin's MCP servers on
the network (`.mcp.json`) come along when an admin installs it, and those that run as a program too when the MCP
runner is on (ones that run a program shipped inside the plugin are left out). Repositories are read from GitHub or GitLab (or the archive link) through the same guard as web
tools: public addresses only.

### The MCP runner

MCP servers that run as a program run in a container of their own, the MCP runner
([`mcp-runner/`](https://github.com/adeelahmad/lense/tree/main/mcp-runner)), never next to Lens. It's off until you
turn it on: add `COMPOSE_PROFILES=mcp` to Lens's `.env` and run the install command again (or
`docker compose --profile mcp up -d`). The container is the sandbox: it has none of Lens's data, keys or database, runs
as an unprivileged user with every capability dropped, a read-only image and memory and process limits, and shares a
network only with Lens's backend (and the internet, which `npx` and `uvx` need to fetch the servers). Each server gets
an empty folder as its home and only the environment its extension gives it; Lens talks to the runner over HTTP with
a token the runner writes to a volume only the two share. A server starts on first use, stays up while it's used,
and stops after 15 idle minutes. Installs that don't run on Docker don't have the runner yet.

`ai.extensions: false` turns every extension off.

## Setting up and running the server by chat

Admins also get the server tools: `server_status` (what's set up and what's missing), `find_model_servers`,
`read_settings` and `change_settings` (the processing and AI sections, not the server's hosts, cookies or tokens) and
`create_namespace`, and the people tools: `list_people`, `invite_person` (an account, optionally with a role in one
namespace; Lens emails the sign-in link when mail is set up) and `change_person` (a role in a namespace, or disabling
and enabling an account). Removing an account for good stays in the web app and the API. In an ordinary conversation
their changes are approval cards. In a **setup conversation**
(`POST /api/v1/chats` with `"kind": "setup"`, or **Finish with the assistant** in the setup wizard once a model is
connected) the admin has asked the assistant to set the server up, so it makes the changes itself and says what it
changed. Every change is audited with `assistant` in its detail, and all of them stay changeable in Settings.
Turning telemetry on or off, inviting people and changing their access always ask first, and so does every change once the answer has read text from outside
Lens ([third-party content](#third-party-content-and-prompt-injection)).

### Routine choices

The assistant makes routine choices for you instead of asking. The first is where files sent in a conversation go:
`import_files` without a namespace picks one from the namespaces you can add to, judging from their descriptions,
what's in them lately, the file names and your message. You're asked only when it isn't sure: below
`decisions.act_above` (0.8) confidence it lists the namespaces, its best guess first.

A **decision model** answers these: Jev, typesafe.ai's System One, which takes a few hundred milliseconds and costs a
fraction of an LLM call. Set its key in **Settings → AI assistant → Routine choices**, or `TYPESAFE_API_KEY` in `.env`.
Without a key, or if it can't be reached, the LLM provider decides. `decisions.engine` is `auto` (the default), `jev`,
`laya`, `llm`, or `off` to always be asked.

**Laya** (`laya`) is a decision model that runs on your own machine, free and offline: the Laya typed decision models
on MLX ([laya-mlx](https://github.com/mizorewww/laya-mlx)), which answer the same questions as Jev in tens of
milliseconds. Pick it in Routine choices with one of three models (`decisions.laya_model`): `aac6fef/laya-mlx`
(English, the default), `aac6fef/laya-multilingual-mlx` (many languages, faster) or `aac6fef/laya-typed-decisions-mlx`.
MLX needs a Mac with Apple Silicon. Run natively on one, Lens fetches laya-mlx and the model itself (Settings →
Components) and answers in its own process. Lens in Docker on a Mac runs in a Linux VM that can't reach the Apple GPU,
so run a Laya server on the Mac, which fetches what it needs on first start:

```bash
uv run lens decide-server          # http://127.0.0.1:8790/v1; --model and --port to change them
```

and set **Laya server** (`decisions.laya_url`) to `http://host.docker.internal:8790/v1`. Elsewhere the setting says
Laya isn't available, and the LLM provider takes the choices. **Test** takes one made-up choice and says who answered.

## Collections, batch runs and collection reports

**Collections** are named sets of assets. A collection is either a filter that stays up to date (namespaces,
people, entities, dates, a search, audio/video/text, status) or a fixed list. Each person gets their own, and
they can be shared. A collection only ever shows the assets the viewer can read. Collections scope chat, batch runs
and reports.

**Batch runs** apply a pipeline, some steps, or one template to many assets. The assets can come from a list, a
collection, a namespace, a filter, an entity or a person.

- `POST /api/v1/batches/estimate` answers first, with:
  - how many assets (by kind) and hours
  - rough processing time
  - LLM calls and tokens, plus cost once `ai.price_in` and `ai.price_out` are set
  - how many existing outputs would be replaced
  - how many matching assets are skipped because you can't change them
- Runs bigger than `ai.confirm_over_recordings` (or `ai.confirm_over_cost`) need the confirmation typed, e.g. `RUN 214`.
- `"sample": 3` runs three assets first; `/continue` then runs the rest.
- Batches can be paused, resumed, cancelled and have their failures retried.
- `/results` returns one table of everyone's outputs. List fields become rows, so you get every action item from every
  call, and `/results.csv` or `/results.md` exports it.
- `/combine` writes a collection report: one overview from all those results, citing each asset as [n].
