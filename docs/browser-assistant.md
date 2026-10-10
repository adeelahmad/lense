# The assistant in your browser

**Status: built** in the Lens extension for Chrome and Edge: the side panel, where you sign in and chat with the
assistant, and the assistant using that browser on the sites you allow. Firefox (in its sidebar) and Safari (in a tab)
run the same code, but only Chromium is covered by the end-to-end test so far. Webmail attachments behind a download
button rather than a link are not handled yet (Refine later).

Ask the assistant from your browser, and let it do things there for you: "check my email and import the important
attachments", "find last month's invoice on the supplier's site and put it in Accounts", "save this page". It opens
pages, reads them, clicks and types in your own browser, signed in wherever you are, and shows you each step as it
goes. Anything that sends, deletes, buys or posts waits for your yes.

Lens looks in what it already has first. Mail from an account connected as a source (IMAP), files you imported and
pages you captured need no browsing; the browser is for what Lens can't reach itself, such as webmail without IMAP,
or a site with no API.

## The side panel

Open it from the extension's popup (**Ask Lens**), by right-clicking any page (**Ask Lens…**), or with a shortcut you
give it in your browser's extension shortcuts (browsers suggest at most four per extension, and the clipper has them).

**Signing in** takes one click. The address is filled in from the clipper's Lens; press **Sign in**, let the extension
reach that address when the browser asks, and allow the "Lens browser extension" on Lens's own sign-in page, where you
are signed in with your passkey. Nothing to copy or paste: the extension signs in like any app (OAuth, see
[Authentication](authentication.md#oauth)), so it needs **Let apps and AI assistants sign in** on, in Settings → API
keys (on by default). Its tokens stay on this device and renew themselves; **Sign out** hands them back to Lens, and
you can also take its access away under API tokens → Apps with access.

**Asking** works as in Lens's chat, and it's the same conversation: **Open in Lens** picks it up there, and **New**
starts another. Answers cite their sources (a citation opens the moment it came from), the steps the assistant took
are listed above the answer, and anything it proposes (an import, say) waits in the panel for **Approve** or
**Decline**. <kbd>Enter</kbd> sends, <kbd>Shift</kbd>+<kbd>Enter</kbd> starts a new line and <kbd>Esc</kbd> stops the
answer.

Firefox shows the panel in its sidebar (also under **View → Sidebar → Ask Lens**) and signs in the same way. Safari
has no side panel and doesn't let extensions show a sign-in page, so there **Ask Lens** opens in a tab, and you sign in
with a write API token you make in Lens (your account's API tokens).

## Letting it use your browser

While the side panel is open, **Let Lens use this browser** (under the question box, on unless you turn it off) lets
the assistant work in that window when you ask it to:

- **A site is used only once you allow it.** The first time the assistant wants to read or act on a site, or open an
  address there, the panel asks **Let Lens read and act on mail.example.com?** Allow, and the browser may ask too, for
  the extension's access to that site. Allowed sites are listed in the extension's options, under The assistant, where
  you remove them again.
- **Anything consequential waits for your yes.** A click or a submit that sends, deletes, buys, books, posts, shares
  or changes an account shows what it is about to do (**Click “Send” on mail.example.com?**) with **Yes, do it** and
  **No**. It asks when the assistant marks the action consequential, or when the button's words say so (send, delete,
  pay, order, post, submit, confirm, unsubscribe...), or when the form posts somewhere. No answer within 80 seconds
  counts as No.
- **Never passwords or card numbers.** It doesn't type into password fields, card number or security code fields,
  or file pickers, and doesn't see what's in them: you type those.
- **You see each action as it happens**, in the answer it's for: opening, reading, clicking, typing, downloading,
  with ✓ when done and ✗ when not (and why). An app using your browser through the API shows the same way.
- **Pages are third-party text.** What a page or an email says reaches the assistant marked as material to read, never
  as instructions; opening an address on a site you haven't allowed asks first, so what it read can't be carried
  off in a link.

Files the assistant downloads (an attachment, a PDF behind a sign-in, or the page itself as Markdown) are attached to
the conversation like a file you dropped in, and it puts them in a namespace with `import_files`, which waits for
your **Approve**.

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

- Webmail attachments behind a download button (no link to read), and files a site hands over from another site
  after a redirect, which need that site allowed too.
- Pages inside frames (other than the page's own shadow trees), and real input events through the browser's
  debugger for pages that ignore synthetic ones.
- Safari, where the panel is a tab of its own: the assistant picks the page's tab from `browser_tabs` today; it
  should default to the tab you were on before.
- Ask about the page you're on, or the text you selected, straight from the right-click menu.
- Pick the model, or the namespaces to look in, in the side panel (the web app's chat has both).
- The assistant's browser for conversations elsewhere (WhatsApp, Slack, Telegram) while the side panel is open, as
  a setting that's off by default.
- Keep-alive events in the chat stream while an action waits for you, for proxies that cut quiet streams short.
- Browser tools over MCP (the MCP server is read-only today).
