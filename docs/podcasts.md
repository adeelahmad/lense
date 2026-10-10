# Podcasts

**Status: in progress.** The backend that writes a cited, fact-checked script and records it in two voices, its API
and its MCP tools are built. The web app's dialog and episode page are planned (see the [dev log](https://github.com/adeelahmad/lense/blob/main/DEVLOG.md)).

Pick resources (and passages in them) and Lens makes a two-host learning episode about them: one host explains, the
other asks the questions a smart learner would ask, with analogies and a recap. Every claim in the script cites the
passage it comes from, and a second pass checks each claim against what it cites before anything is published.

## An episode is a recording

An episode is an ordinary recording in the **podcasts** namespace (`podcasts.namespace`), in its **Podcasts**
collection. Each script line is a segment spoken by the host who says it, so the episode is searchable, gets entity
extraction and a summary, and shows on the graph like anything else. What only an episode has (what was asked for,
the excerpts the model read, the outline, the lines' citations, and what the fact-check changed) is kept beside it in
`podcast:<recording id>`. Deleting the recording deletes the episode.

An episode goes in the podcasts namespace only when everyone who can read that namespace can also read every source's
namespace, and no source is in a [vault](encryption.md). Otherwise it goes in the sources' own namespace (when they
are all in one the person may add to), so an episode never shows content to someone who couldn't read its sources.

## How it's made

One job on the episode's recording, one step per stage, visible in Activity and on the event feed:

| Stage | What happens |
|---|---|
| gathering | The picked resources and passages become numbered excerpts. A long resource is cut to fit `podcasts.context_chars`, preferring passages that mention the prompt's words and spreading the rest over the whole resource. A picked passage comes with two lines either side. |
| planning | The model outlines the episode: key ideas, learner questions, 2 to 4 connections between excerpts and a recap, each tied to excerpt numbers. |
| writing | The model writes the script as lines of `{speaker, kind, text, citations}`. Citations to excerpts it wasn't given are dropped. |
| fact-checking | Every claim and recap line is checked against the excerpts it cites. A partly supported line takes the checker's correction; an unsupported one is rewritten once and checked again, or dropped, as is a claim with no citation. Every change is recorded. |
| recording the voices | Each line is read aloud in its host's voice by the [text-to-speech provider](speech-providers.md) (Settings › Voice: an OpenAI-compatible server such as a local Kokoro, ElevenLabs or Deepgram), the clips are joined with short pauses, and the loudness is evened out (-16 LUFS) into one MP3. Clips are kept, so making an episode again only reads changed lines. Without text to speech this stage is skipped and the episode stays a script. |
| publishing | The lines become the recording's transcript, timed where each line is in the audio (or at `podcasts.words_per_minute` without audio), and the audio becomes the recording's media. |

Then the usual analyze, embed and summarize steps run. Workers run podcast steps when their `workers.steps` list has
`podcast` (or `llm`).

## API and MCP

| | |
|---|---|
| `POST /api/v1/podcasts` | make an episode: `selection` (`recordings`, and `excerpts` as `{recording, idx}` lines), `prompt`, `length` (minutes), `style` (deep-dive, recap, debate, beginner), `title`, `voices` (`{a, b}`). Answers 202 with the episode's id, its job (watch it on `GET /api/v1/events`), its namespace and why it went there. 404 for a source you can't read; 400 when there's nowhere you may put it |
| `GET /api/v1/podcasts` | episodes in the namespaces you can read, newest first |
| `GET /api/v1/podcasts/{id}` | an episode: status, the request, excerpts, outline, lines with citations and times, fact-check changes, audio, and the job making it now |
| `POST /api/v1/podcasts/{id}/regenerate` | make it again from the same request (editors) |
| `DELETE /api/v1/podcasts/{id}` | delete it and its audio (editors) |

The audio is the recording's (`GET /api/v1/recordings/{id}/audio`), and so are sharing and the transcript. Agents
use the MCP tools `create_podcast` (resources as `recording_ids`, lines as `"<recording_id>:<line>"`) and
`get_podcast` ([MCP](mcp.md)).

## The prompts are templates

The plan, write and fact-check prompts are prompt templates (Templates in the web app) called **Podcast: plan**,
**Podcast: write** and **Podcast: fact-check**, made the first time an episode is made. Save a new version of one to
change what every later episode is asked; each episode records which versions made it.

## Settings

| Setting | Default | |
|---|---|---|
| `podcasts.namespace` | `podcasts` | Where episodes go (made by an admin's first episode) |
| `podcasts.host_a`, `podcasts.host_b` | Alex, Sam | The hosts' names: A explains, B asks |
| `podcasts.context_chars` | 24000 | How much source text the model reads |
| `podcasts.max_minutes` | 30 | The longest episode someone can ask for |
| `podcasts.words_per_minute` | 150 | Speaking pace, for the script's length and its timing |
| `podcasts.model` | none | A model for the podcast's LLM calls (none: `llm.model`) |
| `podcasts.voice_a`, `podcasts.voice_b` | none | The hosts' voices (none: `voice.tts_voice` for A, and onyx, ElevenLabs' Sarah or Deepgram's aura-2-orion-en for B) |

## Privacy and cost

The LLM calls go to the configured model (`llm`) and the voices to the text-to-speech provider, so nothing leaves the
machine unless one of them is a cloud service. The audio and its clips are kept under `data_dir/podcasts/<id>`,
encrypted with the namespace's key when encryption at rest is on. Their
cost lands in the activity ledger against the episode and its namespace, and a namespace's [budget](budgets.md) holds
the job when it's over.

## Refine later

- Write long episodes in sections, for small local models.
- Check claims in batches when a script has many.
