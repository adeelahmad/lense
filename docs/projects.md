# Projects

A project is a goal you work toward with Lens's assistant: a treehouse to build with your son, a house move, a client's
case, a piece of code. You give it a name, the goal in your own words, instructions for how to work, and the namespaces
it draws on. Every conversation in it reads all of that, so you don't explain the project again each time.

Status: **built**: projects with a goal, instructions and one or more namespaces; a coordinator conversation for the
project as a whole, told about every thread; threads, each a conversation in the project's namespaces, marked done when
finished; your own conversations brought in or taken out; through the REST API and MCP. **Planned**: the coordinator
starting threads with a brief and reading them, scheduled threads, a Projects page in the web app, chat rooms and email
routed to a project, members sharing one, and a project's own memory.

## What a project is

| Field | What it is |
| --- | --- |
| name | Up to 120 characters; its coordinator conversation is called after it |
| goal | What it is for, in your words (up to 4000 characters): every answer in the project reads it |
| instructions | How the assistant should work in it, such as "keep it cheap" or "my son is nine" (up to 4000 characters) |
| namespaces | One or more namespaces you can read (up to 20): its conversations are scoped to them |
| coordinator | Its own conversation, about the project as a whole |
| archived | Archived projects are listed apart; nothing in them changes |

A project is yours alone, like your conversations: nobody else sees it, admins included. Its conversations answer
the way every conversation does ([Chat](assistant.md#chat)): cited from the archive, with the tools you may use, and
anything that changes data or settings waits for your approval. What the project adds is context:

- Every answer in it is told the project's name, goal and instructions, the namespaces it draws on, and whether this is
  the coordinator or one of its threads.
- The coordinator is also told about the threads, the most recent first: each one's title, whether it's done and its
  last message. What threads said can quote documents, so it reaches the model marked as data, never as instructions
  ([Prompt injection](assistant.md#third-party-content-and-prompt-injection)).

## Threads

A thread is a conversation in the project, about one part of the work: "Wood and tools", "Plan the weekend". It is
scoped to the project's namespaces, titled after its first answer unless you name it, and shows in Chat like any
other conversation. Mark it done when it's finished; it stays to read and to go on with. You can also bring one of
your conversations into a project (it draws on the project's namespaces from then on) or take a thread out (it stays
in Chat). Changing the project's namespaces changes every one of its conversations' scope.

Deleting a project keeps its conversations, as ordinary ones in Chat. Deleting its coordinator conversation makes a
new one the next time the project is opened.

## The API

| Call | What it does |
| --- | --- |
| `GET /api/v1/projects` | your projects, the most recently changed first (`?archived=true`: only archived ones) |
| `POST /api/v1/projects` | start one: `name`, `goal`, `instructions`, `namespaces` |
| `GET /api/v1/projects/{id}` | one, with its threads (`thread_list`) |
| `PATCH /api/v1/projects/{id}` | change its name, goal, instructions or namespaces, or archive it |
| `DELETE /api/v1/projects/{id}` | delete it; its conversations stay |
| `GET /api/v1/projects/{id}/threads` | its threads, the coordinator among them |
| `POST /api/v1/projects/{id}/threads` | a new thread (`title`), or bring in one of your conversations (`chat`) |
| `PATCH /api/v1/projects/{id}/threads/{chat}` | mark a thread done, or not (`done`) |
| `DELETE /api/v1/projects/{id}/threads/{chat}` | take a thread out of the project |

Ask in a thread or the coordinator as in any conversation: `POST /api/v1/chats/{chat}/messages`
([API](api.md)). Over [MCP](mcp.md), `list_projects`, `read_project`, `create_project` and `update_project` do the
same for agents; the last two need a token with the write scope.

## Refine later

- The coordinator starting threads with a brief, reading them and marking them done, as its own tools.
- Scheduled threads: a prompt asked on a schedule, in a new thread each time or the same one.
- A Projects page in the web app.
- Chat rooms, Telegram, Slack and email routed to a project.
- Members sharing a project, a project's own memory, and its outputs (notes, files) listed together.
