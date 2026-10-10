# The assistant in your browser

**Status: in progress.** Built: the server side, below: sessions, the assistant's browser tools and the API. In
progress: the Lens extension's side panel, where you chat with the assistant and it uses your browser.

Ask the assistant from your browser, and let it do things there for you: "check my email and import the important
attachments", "find last month's invoice on the supplier's site and put it in Accounts", "save this page". It opens
pages, reads them, clicks and types in your own browser, signed in wherever you are, and shows you each step as it
goes. Anything that sends, deletes, buys or posts waits for your yes.

Lens looks in what it already has first. Mail from an account connected as a source (IMAP), files you imported and
pages you captured need no browsing; the browser is for what Lens can't reach itself, such as webmail without IMAP,
or a site with no API.

## How it works

The Lens extension's side panel signs in to your Lens and connects a **session**. While the side panel is open, a
question you ask in it can use that browser: the assistant gets the browser tools, and each action it takes goes to
the side panel, which carries it out and sends back what happened. Close the side panel and the assistant can't use
the browser any more.

| Tool | What it does |
| --- | --- |
| `browser_tabs` | the tabs open in that browser window, and which one you're looking at |
| `browser_open` | open an address: in the tab you're looking at, another tab, or a new one |
| `browser_read` | the page's text, with its links, buttons and fields, each numbered (`e12`) to act on |
| `browser_click` | click a link, button, tab or checkbox |
| `browser_type` | type into a field, or pick an option of a list; optionally press Enter after |
| `browser_press` | press a key (Enter, Escape, Tab, arrows) or scroll (PageDown, PageUp, Home, End) |
| `browser_import` | download a file with your browser (an email's attachment, a file behind a sign-in), or save the page as Markdown, and attach it to the conversation; the assistant then imports it into a namespace with `import_files`, which waits for your approval like any import |

An answer that uses the browser may take up to **Tool steps when using the browser** steps (`ai.browser_steps`, 25
by default), since it reads and clicks its way through.

## Only you

- A session is yours: only you see its actions and answer them, and only your own conversations and API calls use it.
- The browser does what the side panel allows. It acts only on sites you allowed, asks you before anything
  consequential, never types passwords or card numbers, and shows each action as it happens.
- What a page, an email or a file says is third-party text. The assistant reads it as material, never as
  instructions, and is told never to put what it read into an address or a field unless you asked for exactly that.
- An admin can turn it off for everyone: Settings → AI assistant → **Let the assistant use a connected browser**
  (`ai.browser`). Off, side panels can't connect and the assistant has no browser tools.

## For agents and scripts

Everything the side panel does goes through the API, with an API token or an app's token that can write:

| | |
| --- | --- |
| `POST /api/v1/browser/sessions` | connect a browser (`{label}`): its session |
| `GET /api/v1/browser/sessions` | your browsers connected now |
| `GET /api/v1/browser/sessions/{id}/actions` | server-sent `action` events `{id, tool, args, chat}` for that browser to carry out; `once=true` sends what's waiting and closes |
| `POST /api/v1/browser/actions/{id}` | what the browser did: `{ok, result}` or `{ok: false, error}` |
| `POST /api/v1/browser/sessions/{id}/run` | do one thing in your connected browser and wait for it: `{tool, args}`, the browser tools without `browser_` |
| `DELETE /api/v1/browser/sessions/{id}` | the side panel closed |

A question sent with `browser` (the session's id) on `POST /api/v1/chats/{id}/messages` can use that browser.

## Refine later

- The assistant's browser for conversations elsewhere (WhatsApp, Slack, Telegram) while the side panel is open, as
  a setting that's off by default.
- Keep-alive events in the chat stream while an action waits for you, for proxies that cut quiet streams short.
- Browser tools over MCP (the MCP server is read-only today).
