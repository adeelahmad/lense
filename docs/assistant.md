# Chat, collections and batch runs

## Chat

Conversations belong to one person and can be scoped to namespaces, recordings, collections, speakers and dates. A
collection in the scope is read each time the assistant answers, so it draws on the collection's recordings as they are
then.

- **Retrieval:** the question's keywords go through the full-text index (English stemming), limited to namespaces the
  person can read. Hits are widened to their neighbouring lines and numbered.
- **Answers:** the model answers only from those excerpts and cites them as [n]. Each citation carries the recording
  and timestamp.
- **Streaming:** answers arrive over server-sent events: `passages`, then `token`s, then `done`.
- **Stopping:** Stop asks the server to end the answer after the piece or tool step it's on (whichever server process
  is writing it); what came before is saved, marked stopped. A model call already under way finishes first.
- **Reopening:** a conversation's answers keep the tools the assistant used (and with what), any notice (the model
  couldn't use tools), the error when there was no answer, and their latest source check.
- **Editing a question:** Edit on a question you asked asks it again as edited; the answer and everything after it
  are replaced (`POST /api/v1/chats/<id>/messages` with `edit`).
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
  stored. A recording's transcript and document keep their own selection toolbar, whose Ask in chat goes to the
  recording's chat.
- **No model configured:** chat returns the best-matching passages instead. Anyone signed in can see whether a model
  is set up, and which (`GET /chats/capabilities`), so the app says so before the first question.

Retrieval is keyword-based for now; vector search is not built yet.

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

Home opens in **assistant mode** (one field and a big mic, like a search page) when the archive has any content, and
on the overview when it's empty; the switch at the top right remembers the person's pick in the browser. The field
opens a new conversation over everything the person can read (`/chat?global=1`, plus `q=` with what was typed); the
mic opens one in voice mode (`/chat?global=1&voice=1`). Voice mode listens, sends what was heard, reads the answer
aloud (without citation marks) and listens again, until the mic is tapped off or nothing is said twice in a row. The
chat composer's mic turns it on in any conversation.

Voice goes through one hook, `useVoice()` in `nextjs-frontend/lib/voice.ts` (`listen`, `speak`, `stop`).

- **Hearing.** When the server has a speech-to-text engine (SenseVoice or Whisper, which it fetches for itself, see
  [Components](components.md)), the browser records the mic and the server turns it into text with that engine:
  speech stays on the server, and it works in every browser that can record (Firefox too). What's been heard so far
  shows in the field while talking; a pause of about a second ends the turn, and nothing said for seven seconds ends
  listening. Without a server engine, or with `voice.input: browser`, the browser's own recognition listens (Chrome,
  Edge and Safari; Chrome sends the audio to its own speech service).
- **Speaking.** Answers are read by a speech model when one is set (`voice.tts_model`: any OpenAI-compatible
  `/audio/speech`, such as Kokoro-FastAPI, LocalAI or OpenAI's `tts-1`, on `voice.tts_base_url` or the LLM provider);
  else by the browser.

Settings → AI assistant → Voice holds these. The API: `GET /voice` (what the server does; it also starts loading the
engine), `POST /voice/transcribe` (a clip as the raw body; nothing is kept), `POST /voice/speak` (`{text}`; 204 when
the browser should read it).

## The assistant's tools

When the configured model supports function calling, chat becomes an agent.

- **What it can look things up with:** transcripts (including on-screen text), recordings, transcript passages,
  outputs, entities and their mentions and timelines, the knowledge graph, and speakers.
- **Limits:** everything is limited to what the person can read and to the conversation's scope, and capped by
  `ai.max_steps` and `ai.max_transcript_reads`.
- **Citations:** every moment a tool returns is numbered, so answers cite [n] across everything the assistant read.
- **Visible steps:** each tool call streams as a step, for example 'Searched for "refund": 42 matches'.
- **Approvals:** running a template on recordings and changing entities (merging, renaming, retyping, describing one
  and the other ways it's said, hiding one, or adding one to a namespace's fixed list) don't happen directly.
  They become approval cards (with the batch estimate) that the person approves, approves on a sample, or declines
  (`POST /api/v1/approvals/<id>`). Viewers only get the read tools.
- **Check sources** (`POST /api/v1/chats/<id>/messages/<id>/check`) re-checks every cited claim against the lines it
  cites, and lists sentences that cite nothing.
- **Fallback:** a model that can't call tools falls back to search-and-answer, with a notice. A model that answers
  straight away without looking anything up gets the same, when the archive has passages that match.
- **Files:** attach files to a message with the clip, or by dropping or pasting them on the box. They upload as you
  type (`POST /api/v1/uploads` with `hold`), stay out of the archive, and the assistant puts them in a namespace
  with `import_files` (`attachments` on `POST /api/v1/chats/<id>/messages`).
- **Keep typing:** what you send while an answer is being written waits its turn and goes next.

## Setting up and running the server by chat

Admins also get the server tools: `server_status` (what's set up and what's missing), `find_model_servers`,
`read_settings` and `change_settings` (the processing and AI sections, not the server's hosts, cookies or tokens) and
`create_namespace`. In an ordinary conversation their changes are approval cards. In a **setup conversation**
(`POST /api/v1/chats` with `"kind": "setup"`, or **Finish with the assistant** in the setup wizard once a model is
connected) the admin has asked the assistant to set the server up, so it makes the changes itself and says what it
changed. Every change is audited with `assistant` in its detail, and all of them stay changeable in Settings.
Turning telemetry on or off always asks first.

### Routine choices

The assistant makes routine choices for you instead of asking. The first is where files sent in a conversation go:
`import_files` without a namespace picks one from the namespaces you can add to, judging from their descriptions,
what's in them lately, the file names and your message. You're asked only when it isn't sure: below
`decisions.act_above` (0.8) confidence it lists the namespaces, its best guess first.

A **decision model** answers these: Jev, typesafe.ai's System One, which takes a few hundred milliseconds and costs a
fraction of an LLM call. Set its key in **Settings → AI assistant → Routine choices**, or `TYPESAFE_API_KEY` in `.env`.
Without a key, or if it can't be reached, the LLM provider decides. `decisions.engine` is `auto` (the default), `jev`,
`llm`, or `off` to always be asked.

## Collections, batch runs and collection reports

**Collections** are named sets of recordings. A collection is either a filter that stays up to date (namespaces,
speakers, entities, dates, a search, audio/video/transcript, status) or a fixed list. Each person gets their own, and
they can be shared. A collection only ever shows the recordings the viewer can read. Collections scope chat, batch runs
and reports.

**Batch runs** apply a pipeline, some steps, or one template to many recordings. The recordings can come from a list, a
collection, a namespace, a filter, an entity or a speaker.

- `POST /api/v1/batches/estimate` answers first, with:
  - how many recordings (by kind) and hours
  - rough processing time
  - LLM calls and tokens, plus cost once `ai.price_in` and `ai.price_out` are set
  - how many existing outputs would be replaced
  - how many matching recordings are skipped because you can't change them
- Runs bigger than `ai.confirm_over_recordings` (or `ai.confirm_over_cost`) need the confirmation typed, e.g. `RUN 214`.
- `"sample": 3` runs three recordings first; `/continue` then runs the rest.
- Batches can be paused, resumed, cancelled and have their failures retried.
- `/results` returns one table of everyone's outputs. List fields become rows, so you get every action item from every
  call, and `/results.csv` or `/results.md` exports it.
- `/combine` writes a collection report: one overview from all those results, citing each recording as [n].
