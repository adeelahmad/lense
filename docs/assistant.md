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
  record, [below](#what-each-answer-records)) and `done`; after a conversation's first answer, `title`
  ([Titles](#titles)).
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
  automations go on from the version showing. In the web app, Edit on a question and Regenerate on an answer (its menu:
  Regenerate with… another model) make a version, which streams in place; `‹ 2 / 3 ›` under a question or an answer
  with versions moves between them (the arrow keys too, while it has focus), showing what followed each, and the next
  question goes on from the version showing. Approvals proposed in one version show in that version.
- **Long conversations:** the model reads the latest six messages word for word. Once eight more have built up
  behind them, they're folded into a running summary kept on the conversation (one model call, after an answer), and
  every answer gets that summary plus the messages it doesn't cover yet. Room conversations (chat-rooms.md) go on for
  good, so they rely on it most. A summary covers the version it was written on: on a version that leaves what it
  covers, the model reads that version's messages, and the next summary is made from them. A conversation that fills
  half of the model's context window (**Chat model's context**, `ai.context_tokens`, 16000 when empty; about four
  characters a token) is folded in early, before the next answer, whatever the count.
  **Model for summaries** (`ai.compact_model`) writes them with a smaller, cheaper model on the same provider; empty,
  the chat's model does. Settings › AI assistant › Summarise long conversations (`ai.compact`, on) turns it off; off,
  or with no model, only the last six messages are read.
- **Titles:**{#titles} a conversation nobody named is titled with its first question's words as soon as it's asked,
  then, after the first answer, the model gives it a short title of a few words from the question and the answer
  (streamed as `title` at the end of the answer's stream). It's one small call, written by **Model for summaries**
  (`ai.compact_model`) when set, else the chat's model, and counted in the activity ledger for the conversation
  (`chat:<id>`), not for the answer. Editing the first question titles it again. A conversation you name, when you
  start it (`POST /api/v1/chats` with `title`) or later (rename in the list, `PATCH /api/v1/chats/<id>` with `title`),
  keeps its name; `title_by` on a conversation says which it is (`assistant` or `person`). `POST
  /api/v1/chats/<id>/title` has Lens title one now from how it starts, even one you named, and the title is Lens's
  again; agents do both with the MCP tool `rename_chat` (a `title`, or none for Lens's). Settings › AI assistant ›
  Title conversations (`ai.auto_title`, on) turns the model's titles off: a conversation then keeps its first
  question's words. Without a model, or when the call fails or the budget's hard limit stops it, the first question's
  words stay too. Chat rooms, setup conversations and automations' conversations keep the names Lens gives them.
- **/clear and /compact:** typed as a message, in the app or in a chat room (`Lens /clear`). `/clear` makes the
  assistant start afresh: the messages stay to read, but the model no longer sees them or their summary.
  `/compact` folds the whole conversation into its summary now, and `/compact keep the dates and owners` tells the
  summary what to keep; the reply shows the summary. Both work with automatic summaries off. Neither command, nor its
  reply, is read by the model afterwards.
- **Changed access:** old citations are filtered by the person's current access when a conversation is reopened.
- **Choosing the model:** a conversation can use any model an admin offers (`llm.chat_models`, else whatever the model
  server lists); Regenerate with… writes a new answer with a different one, beside the old. Each answer records the
  model that wrote it.
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

### Copy and export

A conversation, or one message of it, comes out as a file to keep or hand on: `GET
/api/v1/chats/<id>/export.md`, `export.pdf` or `export.json`. They hold the version showing; `?message=<mid>` holds
one message (an answer comes with the question it answers), and the JSON export's `?all=true` holds every version.
Citations follow the reader's access now, as `GET /api/v1/chats/<id>` does, and no model is called.

- **Markdown:** the title, a short header (the conversation's address in Lens, when it started, what it draws on in
  words, and the Lens version that exported it), then each question and answer with its time, the model that wrote
  it and its version (version 2 of 3). An answer's citations become numbered footnotes naming the asset, its time or
  page and speaker, a short quote and a link back to that moment in Lens; a web page's citation links the page. The
  tools an answer used are a "Looked up" list with each one's summary.
- **PDF:** that Markdown on a page, printed by Chromium where it's installed, else by anytopdf (installed, on a
  conversion node, or fetched on first use, [anytopdf](configuration.md#anytopdf)), else by LibreOffice. When none
  can run here (Settings › Documents says never to use anytopdf, and nothing else is installed), or printing fails,
  it answers 409 with the reason; the web app can still print the conversation.
- **JSON:** the conversation in OpenAI's chat message format, as other tools and trainers read it, with Dublin Core
  terms (`dc:title`, `dc:created`, `dc:modified`, `dc:creator`, `dc:identifier`, `dc:publisher`). `mapping` holds each
  message by id with its `parent` and `children` (more than one child where there are versions), and `current_node`
  is the last message of the version showing. Each node's `message` is `{role, content}`; an answer that used tools
  has them before it in `tool_messages`: an assistant message with `tool_calls`, then a `tool` message per call
  carrying the step's summary. What only Lens knows is under `lens`: the citations (`[n]` in the text is `n` there,
  with a `url` back to Lens or the web page), the answer's record (`meta`, [above](#what-each-answer-records)),
  `versions`, attached files, and the page a question was asked from. `?format=messages` gives the version showing
  as one `{"messages": [...]}` line instead, the shape fine-tuning reads. The schema is
  [chat-export.schema.json](schemas/chat-export.schema.json).

```json
{
  "@context": {"dc": "http://purl.org/dc/terms/"},
  "format": "lens.chat-export/1",
  "dc:title": "Are ships slow?",
  "dc:identifier": "https://lens.example/chat/4",
  "current_node": "2",
  "mapping": {
    "1": {"id": "1", "parent": null, "children": ["2"], "message": {"role": "user", "content": "Are ships slow?"}, "lens": {}},
    "2": {
      "id": "2", "parent": "1", "children": [],
      "tool_messages": [
        {"role": "assistant", "content": null, "tool_calls": [{"id": "call_2_1", "type": "function",
          "function": {"name": "search_transcripts", "arguments": "{\"query\": \"ships\"}"}}]},
        {"role": "tool", "tool_call_id": "call_2_1", "content": "2 moments"}
      ],
      "message": {"role": "assistant", "content": "Yes, said Ana [1]."},
      "lens": {"citations": [{"n": 1, "source": "said", "recording_id": 5, "title": "Ep 1", "time": "1:15",
        "speaker": "Ana", "text": "They are slow.", "url": "https://lens.example/resources/5?t=75"}]}
    }
  }
}
```

Exports carry each tool call's arguments and summary, never its raw result, which Lens doesn't keep. Agents read and
export the same way over MCP: `list_chats`, `read_chat` and `export_chat` ([MCP](mcp.md)) work on the person's own
conversations.

In the web app, an answer's copy button copies it as Markdown in one click (its citations as footnotes linking back to
Lens, as in the Markdown export); the arrow beside it also offers **Copy text** (without Markdown or citation numbers)
and exports that answer with its question as Markdown, PDF or JSON. A question has its own copy button, shown on hover, and each code block in an
answer has one that copies the code as written. The download icon in a conversation's header exports the whole
conversation. When the server can't make a PDF, the browser's print dialog opens on the conversation (or that one
answer), without the app around it, so Save as PDF still works.

Planned: importing a conversation from its JSON, exporting one to a note, and a styled PDF without Chromium.

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

#### Read a message aloud

Every question and answer in a conversation has a speaker button: one click reads it aloud, another stops it. It goes
through the same `POST /voice/speak` as voice mode, so it uses the speech model and the voice picked in Settings → AI
assistant → Voice, without citation marks or Markdown, and falls back to the browser's own speech when the server has
none. One message is read at a time (starting another stops the first, and so does leaving the conversation). Long
answers are sent in pieces of at most 1,500 characters, cut after a sentence, under every provider's limit; the next
piece is asked for while one plays. Each piece's audio is kept in the page (the 32 most recently played), so playing
a message again doesn't call the speech model again. Each call is a `model.speech` row in the
[activity ledger](activity.md), like voice mode's.

Settings → AI assistant → Voice holds these. The API: `GET /voice` (what the server does; it also starts loading the
engine), `POST /voice/transcribe` (a clip as the raw body; nothing is kept), `POST /voice/speak` (`{text, voice?}`;
204 when the browser should read it), `GET /voice/voices` (the voices a provider reads in).

## The assistant's tools

When the configured model supports function calling, chat becomes an agent.

- **What it can look things up with:** assets' text (transcripts and documents, and on-screen text), assets, passages,
  outputs, entities and their mentions and timelines, dates said as days on the calendar (`find_dates`), the
  knowledge graph, people, and content similar to a text,
  an asset, a topic or an entity (`find_similar`), and what happened in Lens, newest first (`timeline`, the
  [stream of activities](automations.md#timeline-reads); `unreviewed` for what the person hasn't marked reviewed),
  and assets by any attribute: faces seen, people heard, entities, topics, kind, content type, dates and the rest,
  each with the moments that show why it matched (`find_resources`,
  [Finding by any attribute](processing.md#finding-by-any-attribute)). It can also ask which of some labels, content
  types or topics fits an asset or a text, and how sure that is (`classify`, [Classifying](processing.md#classifying));
  it changes nothing.
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
  `ai.max_steps` (Settings → AI assistant, Tool steps per answer; 6 by default) and `ai.max_transcript_reads`, or by
  the tool calls a question allows ([below](#choosing-tools-namespaces-and-a-limit)).
- **Always an answer:** the model is told its step budget. When the steps run out, or it replies with nothing, it is
  asked once more to answer from what it found, with the tools off, and to say what it didn't get to check. A server
  that calls tools anyway gets the conversation again as plain text, without tools. That last ask is one more model
  call, logged in the activity ledger like the others. The same archive read twice in one answer (same tool, same
  arguments) isn't run again: the model is pointed at the result it already has.
- **Citations:** every moment a tool returns is numbered, so answers cite [n] across everything the assistant read.
- **Visible steps:** each tool call streams as a step, for example 'Searched for "refund": 42 matches'.
- **Approvals:** running a template on assets, writing a [spec](notes.md#specs-from-transcripts) from an asset's
  transcript (`write_spec`), changing entities (merging, renaming, retyping, describing one
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
- **Python and bash** (off until an admin turns it on): `run_python` and `run_bash` run code in a sandbox of the
  conversation's own, to compute, analyse or convert files. See [Code sandbox](#code-sandbox).
- **The web:** when an admin turns on web search, `web_search` and `web_read` look up what the archive doesn't hold,
  cited like excerpts ([below](#web-search-and-reading-pages)).
- **Lens itself:** questions about Lens rather than the archive go to `lens_help` ([below](#questions-about-lens-itself)).

### Questions about Lens itself

Ask the assistant about Lens, not only about the archive: "what's the MCP address?", "how do I connect Claude?",
"how do I back up?", "is Telegram on?". It answers with the `lens_help` tool, from two things, and cites both:

- **This server:** its version and addresses (the web app as you're using it, the MCP address, the API, the API tokens
  page, the remote access and HTTPS addresses when they're on), whether apps can sign in with OAuth, the chat model,
  what is turned on (the model provider, search by meaning, chat rooms, Telegram, Slack, email, remote access, HTTPS,
  sensors, file shares, encryption, backups, update checks, telemetry, analytics...), and your own access: admin or
  not, and the namespaces you read and edit. Admins also get the settings page of each. Nothing secret: no keys,
  tokens or model server addresses. Cited as "This Lens server", linking to the web app.
- **Lens's docs:** these pages, cut at their headings and found by words (SQLite FTS5 with English stemming, in
  memory, built on first use and again when a page changes; no model and no network). A question gets the four
  sections that match best; `page` reads one page whole. Every answer also carries the docs' contents, each page by
  its title. Each section is cited as "Docs · MCP server › Connecting" and opens that heading on GitHub.

The docs are read from `LENS_DOCS_DIR`, else the `docs/` folder next to the code: the Docker Compose files mount the
install's own `docs/` read-only at `/docs`, so the assistant reads the docs of the version you run (Cloudron's image
has them too). Without them, the assistant still has this server's facts and the link to the docs online. Agents get
the same tool over [MCP](mcp.md), and the [chat rooms](chat-rooms.md) list its sources with their links.
`ai.disabled_tools: [lens_help]` turns it off.

### Web search and reading pages

Built, off by default. An admin turns it on in Settings → AI assistant → Web search (`web.search`), and the assistant
gets two more tools, for everyone who may use the assistant:

- **`web_search`** searches the web: each result's title, address, site and snippet, numbered like the archive's
  excerpts, so an answer cites [n] across both. `time_range` keeps only results from the last day, week, month or
  year. The step reads 'Searched the web for "solar output": 5 result(s)'.
- **`web_read`** reads a page or an online PDF by its address: its title and text, numbered to cite. `find` keeps only
  the paragraphs that mention some words, with the ones around them. It doesn't save the page; `import_urls` captures
  it into the archive.
- **Citations:** a cited page's chip shows its site ("example.org · Harbour news") and opens the page in a new tab;
  the sources list shows it with its site, and a conversation keeps its web citations when it is opened again (they
  aren't the archive's, so nobody's reads hide them). In [chat rooms](chat-rooms.md) the sources line gives the
  page's address.
- **Providers** (`web.search`): `duckduckgo` needs no key or account. DuckDuckGo has no API, so Lens reads its results
  page with [ddgs](https://pypi.org/project/ddgs/) (MIT), an unofficial client that can stop working when the page
  changes; it is about 30 MB and is fetched the first time an admin picks it (Settings → Components shows it, as
  "DuckDuckGo search"). `searxng` asks your own [SearXNG](https://docs.searxng.org/) instance at `web.searxng_url`,
  whose JSON output must be on (`search.formats: [html, json]` in its `settings.yml`). `brave` and `tavily` use those
  services' APIs with the key in `web.api_key`, kept encrypted like other secrets; searches past their free
  allowance are charged by them.
- **What leaves the server:** what it searches for goes to the provider, and the pages it reads come straight from
  their sites. Like [captured pages](api.md#web-pages), every request goes through Lens's network guard: public
  addresses only (plus the networks allowed for pages, `documents.web_networks`), on the web's ports, every redirect
  checked again, so neither a result nor a redirect reaches this machine's own services. Pages and PDFs are read up
  to 10 MB, and the model reads at most `web.read_chars` of a page's text (20,000 characters by default; `find` picks
  the parts that matter in a longer page).
- **Pages that need scripts:** with `web.render` on (Settings → AI assistant → Web search → Reading pages), a page
  that comes back nearly empty is loaded in Chromium, its scripts run, and read again. That needs Chromium on the
  server (the full edition); the light install reads the page as it comes.
- **Asks first after outside text:** a search or a page read sends words out of Lens, so once an answer has read text
  from outside Lens ([below](#third-party-content-and-prompt-injection)), one the model wrote itself waits for your
  approval. Searching for what you typed, and reading an address you wrote or that a search or page gave back, run at
  once. Once approved, the assistant asks the same thing again and gets what the approved call found, without
  searching twice.
- **Cost:** each search and each page read is a row in the activity ledger (`web.search` with the provider and how
  many results came back, `web.read` with the site), never with the query or the page. Brave and Tavily searches are
  marked as estimates, since Lens doesn't know your plan's price.
- **Agents:** an agent turns it on like any setting, with `PUT /api/v1/settings/web` (admins) or by asking the
  [setup chat](#setting-up-and-running-the-server-by-chat), which waits for your approval before it changes web search.
  Each tool can be switched off in Tools it may use (`ai.disabled_tools`).

Refine later: an opt-in SearXNG service in the Docker Compose file (SearXNG is AGPL, so it would stay an extra),
prices per search for the paid providers, web access per namespace, and keeping recent results to search less.

### Choosing tools, namespaces and a limit

By default (Auto) an answer may use every tool the person has. A conversation, or one question, can instead pick the
tools it may use, how many tool calls it may make, and which namespaces it reads.

- **Tool groups.** `GET /api/v1/chats/tools` lists the groups and the tools in each that the person has, and why a
  group isn't there for them:
    - `archive` (search and read assets);
    - `graph` (entities, topics, the knowledge graph);
    - `web` (searching the web and reading pages, once an admin has set up a search);
    - `notes`;
    - `memory` (a namespace's own assistant);
    - `activity` (the timeline, approvals and automations);
    - `changes` (proposals that become approvals);
    - `files` (importing files and web pages);
    - `code` (the [code sandbox](#code-sandbox): Python and bash, once an admin turns it on);
    - `research` ([deep research](#deep-research): `start_research` plans research that waits for your approval);
    - `extensions`;
    - `server` (admins);
    - `help` (Lens's docs and this server's facts: [questions about Lens itself](#questions-about-lens-itself));
    - `browser`;
    - `mcp:<name>` and `ext:<name>`, one for each of the person's extensions.

    Each tool says its `effect`: `read`, `asks` (it waits for the person's approval) or `out` (it sends data out of
    Lens, as a web search, the browser's open, click and type, and code runs while the sandbox's network is on do). A
    group that isn't available says why (switched off by an admin, no web search is set up, the code sandbox is off,
    for admins, or needs editor access to a namespace) and names the setting that switches it back on (`setting`, such
    as `ai.browser`, `web.search` or `sandbox.enabled`).
- **Picking.** `tools` (on `POST /chats`, `PATCH /chats/<id>` or one question, `POST /chats/<id>/messages`) is a list
  of group and tool names. The model is then offered only those tools, and a tool it calls that it wasn't offered isn't
  run. Null is Auto. An empty list means no tools: the answer is written from a search, as when the model can't use
  tools. A name that is no group or tool the person has is a 400. An admin's switches (`ai.disabled_tools`,
  `ai.browser`, `ai.extensions`) still win over any pick.
- **A limit on tool calls.** `max_tool_calls` (in the same places) caps how many tool calls one answer may make in
  all. The model is told the budget. Calls past it get a reply saying they weren't made, and then the answer is written
  from what was found, with outcome `limit`. With a limit, the rounds of calls may go past `ai.max_steps`, up to the
  limit. It is a whole number from 1 to `ai.max_tool_calls` (Settings → AI assistant, Most tool calls a question may
  ask for; 50 by default, at most 500). `GET /chats/capabilities` gives it as `max_tool_calls`.
- **A scope for one answer.** `scope` on `POST /chats/<id>/messages` narrows that answer alone, with the same keys as
  a conversation's scope. `{}` means everything the person can read, and a namespace they can't read is a 404. The
  conversation keeps its own scope, and a question with a scope isn't narrowed by the
  [namespace picker](#picking-the-namespace-for-a-conversation-over-everything).
- **Remembered.** Each answer records what it was offered and its limits (`tools`, `limits`, `scope`), and each
  question records the options it was asked with ([What each answer records](#what-each-answer-records)). A
  conversation keeps its `tools` and `max_tool_calls`. The person's latest choice (the last `POST /chats` or `PATCH
  /chats/<id>` that set them) is kept on their account, so a new conversation starts with it (`remembered` in `GET
  /chats/tools`) unless `POST /chats` says otherwise.
- **Web and MCP tools reach beyond your namespaces.** The namespaces picked narrow the archive's tools only. A web
  search, an MCP server or the browser reads and sends wherever it reaches.

In the web app, the **Tools** chip beside the composer's scope picks these for the conversation (Enter or a tap opens
it): Auto, a switch for each group (marked "needs approval" or "sends data out"; one you don't have says why, with a
link to its setting for admins) and a limit on tool calls per answer. A conversation not started yet opens on your
latest choice. "All my namespaces" at the top of the scope's namespaces goes back to everything in one tap.

Refine later: tool choice in chat rooms and voice mode, a default choice per namespace, and the model suggesting the
tools a question needs.

## Code sandbox

Built: an admin turns it on in Settings → Code sandbox with **Run the gateway on this machine**, which starts an
[NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) gateway next to Lens and turns the sandbox on
(`sandbox.enabled`, off by default; see [The gateway](#the-gateway)), or turns it on and points Lens at a gateway they
run (`sandbox.gateway`, `openshell-gateway:17670` by default). Then the assistant can run Python and bash for the
people it's turned on for: those who can edit a namespace, and admins (`sandbox.who`: `editors`), or admins only
(`admins`). Viewers never get it.

- **One sandbox per conversation.** The first run in a conversation creates its sandbox, `lens-chat-<id>`, from
  `sandbox.image` (`python:3.12-slim` by default, about 45 MB to download) with `sandbox.cpu` (1 core) and
  `sandbox.memory` (512Mi). It keeps its files between runs in that conversation, and no other conversation sees them.
  A sandbox nobody ran code in for `sandbox.idle_minutes` (15) is stopped; the next run starts it again, files and
  all. One unused for `sandbox.keep_hours` (24) is deleted with its files, and deleting the conversation deletes its
  sandbox at once. Changing the image or the network makes the conversation's next run start a fresh sandbox; CPU and
  memory apply to new sandboxes.
- **The tools.** `run_python` (code) and `run_bash` (a script) each run for at most `sandbox.seconds` (60) and give
  back stdout, stderr and the exit code: the model reads the start of stdout and the end of stderr, up to 10,000
  characters, and is told when more was cut off. `files` copies files attached to the conversation in first, by their
  own names (25 MB each, 100 MB a run). `sandbox_files` lists what the sandbox holds. `share_sandbox_file` copies a
  file out (25 MB at most) for the person to download from the answer: a chart, a converted document, a cleaned
  table. `import_sandbox_file` copies one out as if the person had attached it and puts it in a namespace with
  `import_files`, so it waits for their approval; its type must be one uploads take.
- **In the conversation.** Each run is a step under the answer, folded: opening it shows the code, then what it
  printed (the first 4,000 characters of stdout, the last 4,000 of stderr in red) and how it ended. A file the
  sandbox shared is a button on the answer that downloads it (`GET /api/v1/chats/{id}/files/{file}`, only for the
  person whose conversation it is). Lens keeps those files with the conversation, encrypted with a key derived from
  the server's secret key, at most 20 a conversation, and deletes them with the conversation or the person's account.
- **Nothing of Lens's inside.** A sandbox holds no keys, no database address and no archive: only the code, and the
  files a run copies in. What it prints reaches the model marked as third-party text, like any tool's result.
- **Its network** (`sandbox.network`): `off` (the default) allows no traffic in or out at all; `packages` reaches
  only the Python and npm registries (pypi.org, files.pythonhosted.org, registry.npmjs.org), so `pip install` works;
  `hosts` reaches those and the names an admin lists in `sandbox.hosts` (Hosts it may reach: `api.example.com`, or
  `*.example.com` for its subdomains) on ports 80 and 443. OpenShell resolves only names its policy lists, so there is
  no "any website" setting. Every name is held to public addresses: one that resolves to a private address (Lens's own
  services, your home network, the cloud metadata address) is refused. The model is told what the network reaches.
  With the network on, a run after the answer read text from outside Lens waits for the person's approval (below),
  since code could carry what it read out.
- **How it's kept apart.** OpenShell runs each sandbox as a container under its supervisor: the code runs as an
  unprivileged user with no capabilities, Landlock lets it write only its working folder and `/tmp` and read the
  image's programs, seccomp filters its system calls, and its egress is denied unless the policy above allows it.
- **Cost.** It makes no model calls of its own, and the compute is your machine's. Each run is a row in the
  [activity ledger](activity.md) (`sandbox.run`, with its time, language and exit code), counted for the conversation.
- **Its client.** Lens's own client for the gateway (the `sandbox` Python extra, OpenShell's SDK, Apache-2.0) is
  fetched once the sandbox is on ([Components](components.md)).

### The gateway

Built: the Docker Compose install has a `sandbox` profile that runs OpenShell's gateway (release 0.1.4) next to Lens.
**Run the gateway on this machine** (Settings → Code sandbox, admins) has the machine's upgrade agent add `sandbox` to
`COMPOSE_PROFILES` and start it, as it does for [monitoring](prometheus.md), then turns the sandbox on with
`sandbox.gateway` set to `openshell-gateway:17670`. Turning it off deletes every conversation's sandbox, files and
all, turns the sandbox off and stops the gateway. Agents do the same with `PUT /api/v1/admin/sandbox` (`{"on": true}`)
or the MCP tools `sandbox_gateway` and `set_sandbox_gateway`; `GET /api/v1/admin/sandbox` says how the machine's last
change went and whether Lens reaches the gateway and is let in. With no upgrade agent, Settings shows the line to run
on the machine:

```sh
docker compose -f docker-compose.prod.yml --profile sandbox up -d openshell-gateway
```

- **What it checks first.** It says why the gateway can't run here, and doesn't start it: Lens isn't running from its
  Docker Compose install (a [microsandbox](microsandbox.md), say), the machine isn't 64-bit Intel, AMD or ARM, or its
  Linux kernel has no Landlock 3 (Linux 6.2 or newer, with Landlock turned on). OpenShell also needs Docker 28 or
  newer, which Lens can't see from its container: the start fails with Docker's own message when it's older.
- **The containers.** `openshell-init` makes the gateway's certificates once, in `/var/lib/openshell` on the machine
  (the gateway has Docker mount files from that folder into each sandbox, so it is the same path inside and out);
  `openshell-key` copies the client's certificate to the `sandbox-key` volume, readable only by Lens's backend and
  worker. `openshell-gateway` runs as root to drive Docker through its socket, with no capabilities, a read-only image
  and no telemetry, configured by `sandbox/gateway.toml`. Lens reaches it on a network of their own (`sandbox`) over
  mutual TLS; each sandbox's supervisor reaches it on the machine's `127.0.0.1:17670`, which isn't published to the
  network.
- **Size.** About 300 MB of images on disk (the gateway, the supervisor and the sandbox runtime), downloaded the first
  time it starts, plus `sandbox.image`. Each sandbox is a container with its own CPU, memory and process limits.

The upgrade agent knows the `sandbox` profile from this release on; a machine whose agent is older (it updates itself
with the next release it installs) says so in Settings and shows the line above to run by hand until then.

Refine later: automations' agent runs without a conversation get no sandbox yet; running admins' Python extension
tools in the sandbox so people who aren't admins can write them; asking the person before a run reaches a host that
isn't listed; the gateway's port (17670) as a setting; the gateway on macOS (Docker Desktop doesn't share
`/var/lib/openshell`) and in a [microsandbox](microsandbox.md) (the gateway on the host, or OpenShell's MicroVM
driver); the Podman driver for hosts without Docker; GPUs; a direct sandbox API for agents (today an agent asks the
assistant); one real run against OpenShell in CI (the tests use a fake gateway with OpenShell's own client); showing
an image the sandbox shared inline in the answer, not only as a download; the 20-file cap as a setting.

## Deep research

Built: the web app, the API, MCP, chat rooms and the assistant's tools.
Give the assistant a goal that needs many look-ups, and it comes back with a plan; once you approve it, the research
runs in the background and a cited report comes back in the conversation.

- **Asking.** Turn on the composer's Deep research switch (the telescope; the cursor stays in the box) and type the
  goal, start a question with `/research` (`/research what changed in the capsid work this year?`), send it with
  `research: true` (`POST /api/v1/chats/<id>/messages`), call `POST /api/v1/chats/<id>/research {goal}`, or ask the
  assistant for research: its `start_research` tool (the `research` [tool group](#choosing-tools-namespaces-and-a-limit))
  works from chat, voice and [chat rooms](chat-rooms.md). It needs a language model that uses tools.
- **The plan.** One model call reads the goal and writes up to three follow-up questions, each with answers to pick
  from, and a plan: a title, the sub-questions it will answer, where it will look (`archive`, `graph`, `notes`, `web`,
  `code`: only the sources this person has, and of those only what the conversation's choice of tools keeps), the
  most tool calls it may make, and the report's outline. With it comes an estimate of what it will cost, always
  labelled an estimate: from what each tool call of past research runs cost in the activity ledger, else its likely
  tokens at the model's price in Settings (no figure when the model has no price). The plan is the answer to the
  question, and it waits as an approval of the assistant's (tool `research`).
- **The plan card.** In the conversation the plan is a card: the follow-up questions with their answers to tap (or
  type your own), the sub-questions as a list to reword, add to or remove from, where it looks as ticks (only the
  sources you have), a stepper for its tool calls with the estimate beside it, and the report's outline. Enter
  approves it, with your edits; once you've answered a question, Enter revises it with your answers instead (Approve
  as it is stays one click away). Decline drops it.
- **Approve, edit or revise.** Approve it as it is, or with your edits (`POST /api/v1/approvals/<id>` with
  `decision: approve` and `params`: any of `title`, `sub_questions`, `sources`, `max_tool_calls`, `report_outline`), in
  the conversation or the Approvals page; or send your answers and edits back for one more planning call (`POST
  /api/v1/research/<id>/revise {answers, plan}`), which changes the plan and its approval in place. In a chat room,
  "yes" approves the plan as it is. Decline it and nothing runs. `review: false` on `POST /chats/<id>/research` starts
  it at once, as your own pick.
- **Agents only decline.** An API key, an app or an agent (MCP, the assistant) can decline a plan but not approve it,
  as for every approval, unless an admin turns on `research.agents_skip_review` (off by default): then an agent's plan
  starts without review. The `research` settings are in Settings → AI assistant → Deep research, and
  `PUT /api/v1/settings/research`.
- **The run.** Once approved, one process runs it at a time (a claim it keeps with a heartbeat every 10 seconds;
  another process takes it over after two minutes of silence and goes on from the last finished step), in stages:
    - *gather*: each sub-question in turn, with the assistant's tool loop, its share of the tool calls that are left,
      and the plan's sources' tools alone (read tools only). Searches and page reads on the web, and code runs that
      reach the web, don't ask each time: approving the plan approved them. Any other tool that changes something
      still asks.
    - *write*: one call writes the report in Markdown, following the outline, citing [n] across every finding,
      numbered as one list.
    - *check*: each cited claim is checked against what it cites ([check the sources](#chat)); one that isn't
      supported is rewritten once, or marked "(not supported by its sources)".
    - *done*: the report is an answer in the conversation (`kind: research`), after its last message, with its
      [record](#what-each-answer-records) (`research` is its id; its tokens and cost are the whole run's).
- **Limits.** A person has one run going at a time; the next waits its turn. `research.max_tool_calls` (40) caps a
  plan's tool calls, and `research.max_minutes` (20) a run's time: past it, the report is written from what it found
  and says which sub-questions it didn't get to (outcome `limit`). `research.model` picks another model for research
  (empty: the conversation's).
- **Budgets.** Before each stage the [budgets](budgets.md) of the conversation's namespaces and `all` are checked. One
  that is over holds the run (`held`, saying why) until the budget allows it, or until you let it go on anyway (`POST
  /api/v1/research/<id>/resume`); a budget that skips stops it with what it found.
- **Progress and stopping.** Once approved, the plan card becomes a progress card that reads the run again every few
  seconds, so it carries on after a reload and on a phone: its stage, a bar of the sub-questions done with each one
  ticked off, sources found, tool calls, cost and minutes, why a budget holds it with Go on anyway, and Stop. When the
  report is saved, the conversation shows it. `GET /api/v1/research/<id>` gives its status (planned, declined, queued, running, held,
  done, stopped, failed), stage, and progress: sub-questions done, sources found, tool calls, minutes, and tokens and
  cost so far. `GET /api/v1/research?chat_id=<id>` lists a conversation's. `POST /api/v1/research/<id>/stop` stops it
  after the step it's on (at once while it waits), and what it found so far is saved in the conversation as its
  answer, marked stopped.
- **After.** `POST /api/v1/research/<id>/note` saves the report as a [note](notes.md) in the namespace it looked in
  (`namespace` when there are several), its archive citations as mention links to what they cite, web ones as links,
  and a link back to the conversation (`@[Research: title](chat:12)`), which only you see named. `POST
  /api/v1/research/<id>/podcast` makes a [podcast episode](podcasts.md) from the assets the report cites, about its
  goal. In the web app the report has these as Save as note (asking which namespace when it could go in several),
  Read aloud (with the voice in Settings → AI assistant), Make a podcast, and Export (a Markdown file with the sources
  it cites). Follow-up questions go on from the report.
- **Agents.** MCP has `start_research` (a new conversation unless `chat_id` is given; `namespaces`, `tools`,
  `max_tool_calls`) and `research_status` (the plan and progress, and once it's done the report with its sources and
  their links).
- **Cost.** Planning is one call; gathering makes at most the plan's tool calls, each round a call; writing and
  checking are two or three calls. Every call is in the activity ledger for the conversation and `research:<id>`.

Refine later: sub-questions in parallel, research on a schedule, sharing a report with a namespace's members, research
as an automation action, a report template people can edit, the report posted back into the chat room it was asked
in (today the room gets the plan, and the report is in the conversation in Lens), and one way to read a report aloud
(today its Read aloud and the answer's speaker button both do).

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
  a [web search or page read](#web-search-and-reading-pages) you or a tool didn't give it, other servers' MCP tools
  and Python tools with the network, code in the [code sandbox](#code-sandbox) when its network is on, the changes a
  setup conversation makes itself, and, in
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
  server marks read-only answers at once, the others ask for approval, and an admin switches each on or off and says
  which ask first ([Adding an MCP server](#adding-an-mcp-server)). A server that runs as a program (stdio: `command`,
  `args` and `env` instead of an address, as in Claude's `.mcp.json`, usually `npx` or `uvx`) runs in the MCP runner,
  below.
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

### Adding an MCP server

On **Extensions**, **Add MCP server** (admins) takes a name and either the server's address with the headers it needs,
or the command it runs as with its arguments and environment. **Paste a config** reads the JSON other apps keep their
servers in, one server or many: Claude's `.mcp.json` (or `mcpServers` in its settings), VS Code's `mcp.json`
(`servers`), or a server on its own as its README gives it. A server without a name is named after its package or its
address (`@modelcontextprotocol/server-github` is `github`), and values written as `${NAME}`, which those apps fill in
from their own settings, get a field of their own to type them in, which goes in their place when the server is tested
or saved. Servers on the older SSE transport are left out with a note.
**Test** asks the server for its tools before anything is saved, and Enter adds it. Over the API:
`POST /api/v1/extensions/mcp/read` reads a config (nothing is reached), `POST /api/v1/extensions/mcp/test` lists a
server's tools without saving it, and `POST /api/v1/extensions` saves it as an extension of kind `mcp`.

**Ready-made** lists servers Lens knows. **Browser** adds [the browser](#the-browser) with one click once its container
runs (until then its switch is there to turn it on). **GitHub** puts GitHub's own server
(`https://api.githubcopilot.com/mcp/`) in the form with a field for a personal access token. The list is
`GET /api/v1/extensions/mcp/presets` (admins), each with the spec to save and, for the browser, whether its container
is on and answering.

Header and environment values whose names say they carry a credential (Authorization, API keys, tokens, secrets,
passwords, cookies, sessions) are sealed when the server is saved, with the server's key (as notification targets and
sources are, since an MCP server belongs to the whole install rather than a namespace), bound to the extension, the
value's name and where it goes: the address's scheme, host and port, or the command with its arguments. They're opened
only to reach the server and never shown again: the page, the API and the manifest give the name and `••••`. Saving a
manifest with `••••` keeps the value saved before, unless the server now goes somewhere else (another host, or another
command or package), when it asks for the value again, and a sealed value copied into another extension doesn't open.
Other headers (`X-Team: blue`) stay as they are, and so does a secret written into the address or the arguments, so
put tokens in headers or the environment.

Each server's page lists its tools with a switch and **Asks first**. A tool the server marks read-only runs at once and
the others ask, unless an admin says otherwise; the choices are kept in its manifest (`off`, the tools switched off,
and `ask`, `{tool: true or false}` where it differs from the server's hint), stay when its tools are listed again, and
each change is a new version (`PATCH /api/v1/extensions/{id}/tools` with `{tool, on, ask}`). Whatever the switch says,
an MCP tool still asks first after the conversation read outside text, as every tool that sends to another host does
([Third-party content and prompt injection](#third-party-content-and-prompt-injection)).

Lens opens a new MCP session for each call, except for a server whose spec says `keep: true`, one that remembers
things between calls (the browser's open page): its calls share one session, kept for ten minutes after the last one
and opened again when the server no longer knows it. That session is the server's, shared by everyone whose assistant
uses it, as its address and token are.

### The MCP runner

MCP servers that run as a program run in a container of their own, the MCP runner
([`mcp-runner/`](https://github.com/adeelahmad/lense/tree/main/mcp-runner)), never next to Lens. It's off until you
turn it on: **Run the MCP runner on this machine** under **Command** in **Add MCP server** has the machine's upgrade
agent add `mcp` to `COMPOSE_PROFILES` in `.env` and start it (see [Optional containers](#optional-containers)),
or by hand, add `COMPOSE_PROFILES=mcp` to Lens's `.env` and run the install command again (or
`docker compose --profile mcp up -d`). The container is the sandbox: it has none of Lens's data, keys or database, runs
as an unprivileged user with every capability dropped, a read-only image and memory and process limits, and shares a
network only with Lens's backend (and the internet, which `npx` and `uvx` need to fetch the servers). Each server gets
an empty folder as its home and only the environment its extension gives it; Lens talks to the runner over HTTP with
a token the runner writes to a volume only the two share. A server starts on first use, stays up while it's used,
and stops after 15 idle minutes. Installs that don't run on Docker don't have the runner yet.

### The browser

The browser is a headless Chromium the assistant drives over MCP: it opens pages, reads them, clicks, types and fills
in forms. It is [Playwright MCP](https://github.com/microsoft/playwright-mcp) (Apache-2.0), in a container of its own
(the optional `browser` profile, `mcr.microsoft.com/playwright/mcp`, pinned in the compose file: about 400 MB to
download, and at most 1 GB of memory). It's off until you turn it on: **Run the browser on this
machine** under **Ready-made** in **Add MCP server**, then **Add Browser**, which adds it as the MCP server `browser`
(its tools are `browser__browser_navigate`, `browser__browser_snapshot`, `browser__browser_click` and so on).

- **Its sandbox.** None of Lens's data, keys or database; every capability dropped, a read-only image, memory and
  process limits, and its profile (cookies, history) in memory only, gone when it stops (after an hour unused, or with
  the switch). Its network has no way out but its proxy, `browser-proxy`: the same guard that captures web pages
  (`netguard.py`), on its own, which goes to public addresses on ports 80 and 443 only and refuses this machine, the
  local network and a cloud's metadata address. Lens's backend reaches it at `http://browser:8931/mcp`, and it
  answers only to that name.
- **What asks first.** Going to a page, going back and waiting for one run at once until the conversation has read
  outside text; clicking, typing, filling in forms and anything else that acts on a page ask first, and after the
  conversation read a page every browser tool asks, as every tool that sends to another host does. Running code in
  the browser's own server process (`browser_run_code_unsafe`) starts switched off; turn it on in the server's page
  only if you mean to.
- **One browser for everyone.** Every conversation that uses it drives the same browser, with the same open pages and
  cookies, so a site someone signs in to through it is signed in for the others too until it stops.
- Installs that don't run on Docker don't have the browser yet; by hand, it's `docker compose --profile browser up -d`.

### Optional containers

The MCP runner, the browser, the [code sandbox](#the-gateway)'s gateway and monitoring
([Prometheus and Grafana](prometheus.md)) are containers Lens can run next to it, off until an admin turns one on. The web app can't start containers itself, so the machine's upgrade agent does
([Updates](updates.md)): it adds the profile to `COMPOSE_PROFILES` in Lens's `.env`, or takes it out, and starts or
stops that profile's containers, one change at a time. Without an agent (Lens wasn't installed with `install.sh`), the
switch shows the line to run on the machine instead. Agents use the same switches: `GET /api/v1/admin/services` and
`PUT /api/v1/admin/services/{profile}` with `{"on": true}`, or the MCP tools `services` and `set_service` (admins).

`ai.extensions: false` turns every extension off.

## Setting up and running the server by chat

Admins also get the server tools: `server_status` (what's set up and what's missing), `find_model_servers`,
`read_settings` and `change_settings` (the processing and AI sections, not the server's hosts, cookies or tokens),
`create_namespace`, the news feed tools: `news_feeds`, `subscribe_feed` (follow a feed into a namespace in one step,
[News feeds](processing.md#news-feeds-rss-and-atom)) and `unsubscribe_feed`, and the people tools: `list_people`, `invite_person` (an account, optionally with a role in one
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
Analysis takes routine choices too: what a name the rules can't type is (a person, an organisation, a product...;
[Names the rules can't type](processing.md#entities-and-the-graph-explorer)).

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
