**Email replies.** Lens drafts a reply to an email a watched mailbox brought in, from what that email's namespace
holds: each fact cited to its excerpt, what the archive doesn't say marked `[to confirm: …]`, addressed to the sender
and threaded. Drafts wait to be edited or set aside and nothing is sent; ask through the API or MCP
(`email_replies`, `draft_reply`, `get_reply`, `edit_reply`), or add the new reply step to a pipeline to draft each
new email, skipping mailing lists, no-reply senders and your own mail without a model call. The prompt is the
Reply to an email template ([Email replies](docs/email-replies.md)).
