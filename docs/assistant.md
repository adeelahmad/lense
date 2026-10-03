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
- **Changed access:** old citations are filtered by the person's current access when a conversation is reopened.
- **Choosing the model:** a conversation can use any model an admin offers (`llm.chat_models`, else whatever the model
  server lists); Try another model asks a question again with a different one. Each answer records the model that
  wrote it.
- **No model configured:** chat returns the best-matching passages instead. Anyone signed in can see whether a model
  is set up, and which (`GET /chats/capabilities`), so the app says so before the first question.

Retrieval is keyword-based for now; vector search is not built yet.

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
- **Fallback:** a model that can't call tools falls back to search-and-answer, with a notice.

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
