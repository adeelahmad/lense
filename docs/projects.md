# Projects

A project is a goal you work toward with Lens's assistant: a treehouse to build with your son, a house move, a client's
case, a piece of code. You give it a name, the goal in your own words, instructions for how to work, and the namespaces
it draws on. Every conversation in it reads all of that, so you don't explain the project again each time.

Status: **built**: projects with a goal, instructions and one or more namespaces; a coordinator conversation for the
project as a whole, told about every thread, which starts threads with a brief, reads them and marks them done;
threads, each a conversation in the project's namespaces, marked done when finished; briefs answered in the background;
your own conversations brought in or taken out; schedules that ask a prompt in a new thread each time or the same
one; through the REST API, MCP and the Projects page in the web app. **Planned**: chat rooms and email routed to a
project, members sharing one, and a project's own memory.

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

## The coordinator's tools

In a project's conversations the assistant has the tools of **This project** (one of the groups in the tool picker,
[Choosing tools](assistant.md#choosing-tools-namespaces-and-a-limit)):

| Tool | What it does |
| --- | --- |
| `project_threads` | the project's threads: each one's title, whether it's done, whether an answer is being written, its last message |
| `read_thread` | a thread's last messages |
| `start_thread` | from the coordinator: a new thread with a title and a brief, which is answered there in the background (below) |
| `finish_thread` | mark a thread done, or not done |

So you can say to the coordinator "split this into the wood, the tools and a weekend plan" and it starts a thread for
each, then reads what they found. One answer starts at most three threads. A brief written after the answer read text
from outside Lens (a document, a web page, another thread) could carry that text's instructions, so then
`start_thread` asks for your approval first, like the other tools that would act on it.

## Briefs

A thread can start with a brief: what to do, with everything it needs. The coordinator writes one with `start_thread`;
you or an agent give one with `brief` on `POST /api/v1/projects/{id}/threads` (or MCP `update_project`'s `new_thread`
and `brief`). It is asked as the thread's question and answered in the background, as you: with the tools you have
there (the conversation's choice of them) and the same approvals, every model call counted for the answer in the
[activity ledger](activity.md) and held to your budgets. The thread shows `answering` until the answer is saved, and
Stop (`POST /api/v1/chats/{id}/stop`) stops it between steps. If Lens stops while a brief is being answered, the thread
says so within a few minutes, so it's never left waiting: ask again.

## Schedules

A schedule asks a prompt on a schedule, as a brief: "every Monday at 9:00, what did we get done and what's next?". Each
time it starts a new thread, or, with **The same thread each time**, it goes on in one thread it starts the first time.
The schedule is five cron fields (minute, hour, day of month, month, day of week, or `@daily`, `@weekly`...) in a time
zone; the web app uses yours. It is answered as you, with your tools, approvals and budgets, like any brief. A thread
still answering the last time is left to finish, and the schedule says why it didn't ask. Switched off, or while the
project is archived, a schedule waits; it runs again when it's switched on or the project is brought back. If Lens was
off when one was due, it runs once when Lens is back, within `automations.catch_up` (an hour by default). A project has
up to 20 schedules.

## In the web app

**Projects** is at the top of the conversation list in Chat (`/projects`). **New project** asks for a name, the goal
and the namespaces, starting from the one picked in the top bar; the instructions are in **Edit** on the project's
page. That page shows the goal, a **Coordinator** button that opens the project's own conversation, a **New thread**
field and the threads, open ones first. Type what a thread should work on and press Enter: it is answered in the
background and marked **Working** until the answer is written. Press Enter with the field empty to open a new thread
and ask in it yourself. The box beside a thread marks it done. **Archive** in the project's menu moves it to the
archived list, with Undo. A conversation in a project has a **Project** link at its top, and one answered in the
background shows "Working on it" until its answer arrives. **Ask on a schedule** under **Schedules** adds one: what
to ask, when (from a list, or a cron of your own) and where. The switch beside a schedule turns it off or on, and the
bin removes it, with Undo.

## The API

| Call | What it does |
| --- | --- |
| `GET /api/v1/projects` | your projects, the most recently changed first (`?archived=true`: only archived ones) |
| `POST /api/v1/projects` | start one: `name`, `goal`, `instructions`, `namespaces` |
| `GET /api/v1/projects/{id}` | one, with its threads (`thread_list`) |
| `PATCH /api/v1/projects/{id}` | change its name, goal, instructions or namespaces, or archive it |
| `DELETE /api/v1/projects/{id}` | delete it; its conversations stay |
| `GET /api/v1/projects/{id}/threads` | its threads, the coordinator among them |
| `POST /api/v1/projects/{id}/threads` | a new thread (`title`), or bring in one of your conversations (`chat`); with a `brief`, asked and answered there in the background |
| `PATCH /api/v1/projects/{id}/threads/{chat}` | mark a thread done, or not (`done`) |
| `DELETE /api/v1/projects/{id}/threads/{chat}` | take a thread out of the project |
| `POST /api/v1/projects/{id}/schedules` | ask a `prompt` on a schedule (`cron`, `timezone`), in a new thread each time or the same one (`thread`: `new` or `same`) |
| `PATCH /api/v1/projects/{id}/schedules/{schedule}` | change a schedule, or switch it off or on (`on`) |
| `DELETE /api/v1/projects/{id}/schedules/{schedule}` | remove a schedule; the threads it started stay |

Ask in a thread or the coordinator as in any conversation: `POST /api/v1/chats/{chat}/messages`
([API](api.md)). Over [MCP](mcp.md), `list_projects`, `read_project`, `create_project`, `update_project` and
`schedule_project` do the same for agents; the last three need a token with the write scope.

## Refine later

- Threads answering their briefs at the same time as each other, with a limit per person.
- The coordinator setting schedules itself, after the person's yes.
- Project schedules on the Schedules page, beside routines, automations and the backup.
- Deleting a project from the web app (the API and MCP can), with Undo.
- A project's threads in the Chat sidebar, grouped under it.
- Chat rooms, Telegram, Slack and email routed to a project.
- Members sharing a project, a project's own memory, and its outputs (notes, files) listed together.
