# Email replies

Lens drafts replies to the email it reads, from what your archive says. Each fact in a draft is cited to the excerpt it
came from, what the archive doesn't say is marked for you to fill in, and the draft waits for you:
Lens never sends a reply on its own.

It works on any email Lens has imported: mail that a watched [Gmail or IMAP mailbox](processing.md#gmail-and-google-workspace) brought in, or an
`.eml` file you uploaded. It needs a language model (Settings › LLM provider).

## Drafting a reply

Ask for one email's reply through the API or MCP, with what it should say if you like ("say yes, Tuesday works"):

```bash
curl -X POST "$LENS/api/v1/recordings/42/reply" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"instructions": "Say Tuesday works for us."}'
curl "$LENS/api/v1/recordings/42/reply" -H "Authorization: Bearer $TOKEN"
```

The draft is written in the background, as a job step (`reply`), so it counts against [budgets](budgets.md) and every
model call is in the [activity ledger](activity.md). What comes back:

- `text`: the reply, with `[n]` after each fact taken from source `n`, and `plain`: the same without the citations, as
  it would be sent (the person you answer can't open your archive);
- `sources`: the excerpts the model was given, each saying whether the draft cites it (`used`);
- `gaps` and `unconfirmed`: what the reply needs that the archive doesn't say, written into the draft as
  `[to confirm: …]`, and how many of those marks are still there;
- `to` (the sender, or the address their Reply-To names), `from` (the address the email came in to), `subject`
  (`Re: …`), and `in_reply_to` and `references`, so the reply joins the email's thread; `others` lists the rest of
  who it was for, to answer all of them.

Drafting the same email again replaces the draft and keeps the one before as `previous`.

### What a draft is drawn from

- The email as it was received: Lens reads the message again from its mailbox (or the uploaded file), without the
  quote of earlier mail and the signature. An email whose file can't be read any more is drafted from its text in
  Lens, and its reply won't thread.
- Excerpts of the email's own namespace that match it, found as the [assistant](assistant.md) finds them (by words,
  and by meaning once that's turned on). Never the email itself, and never another namespace: a reply goes to someone
  outside Lens, so it says only what that namespace holds.

### The prompt

Replies are drafted with the **Reply to an email** prompt template, on the Templates page ([pipelines and templates](processing.md#pipelines-and-templates)): change its
tone, language or rules there, and the next draft uses the new version. A request can name another prompt template
(`template`, `version`).

## Drafting every new email

Add a **reply** step to the [pipeline](processing.md) of the namespace a mailbox's watch fills, after the step that
reads the email: each new email gets a draft as it arrives. No pipeline has the step until you add it, so nothing is
drafted, and no model is called, unless you ask.

A pipeline skips mail that isn't one to answer, without a model call: mailing lists, bulk mail, no-reply senders,
auto-replies, delivery reports, and mail sent from one of your own mailboxes. Asked for one email by hand, Lens drafts
it anyway.

## Reviewing, editing and setting aside

`GET /api/v1/replies` lists the email of the namespaces you can read, newest first, each with its reply. By default
(`show=waiting`) it's the mail that may need an answer (no reply yet, and not automatic or your own) and every draft;
`show` takes one status (`draft`, `drafting`, `skipped`, `dismissed`, `sent`, `failed`), `none` (no reply yet,
automatic mail too) or `all`, and `namespace` narrows it to one.

`PATCH /api/v1/recordings/{id}/reply` changes a draft's `text`, `to`, `cc` or `subject` (the text before is kept as
`previous`), or sets it aside (`status: dismissed`) and brings it back (`status: draft`), which is how setting it aside
is undone. Drafting, editing and setting aside need the right to process the email (editors); anyone who can read the
email can read its reply.

## For agents

The MCP server has the same: `email_replies` lists what waits, `draft_reply` drafts one (with `instructions`),
`get_reply` reads it with its sources linked into the archive, and `edit_reply` changes it or sets it aside. Each result
carries `review_url`, where the person reviews the draft. Nothing an agent does sends mail.

## Safety

An email is someone else's words, and may be written to steer a model. It reaches the model marked as data
([what the archive says is data](mcp.md#what-the-archive-says-is-data)), and the system prompt says never to follow
instructions in it. A web address in the draft that neither the email nor the excerpts hold is broken up
(`hxxps://evil[.]example/…`), so an email can't have its reply carry a link of its own making, and a citation to an
excerpt the model wasn't given is taken out. The draft says only what the email's namespace holds.

## Refine later

- Send a reply through the mailbox it came in to, after your yes, and a Replies page in the web app (in progress).
- A signature in Settings; a decision model choosing which mail needs an answer; drafts from a whole thread rather than
  its newest message; replies with attachments.
