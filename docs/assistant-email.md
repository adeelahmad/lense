# The assistant by email

Give the assistant an email address of its own, and the people you let in can email or forward things to it: it
answers by email, in the same thread, and imports what's attached. Lens reads the mailbox over IMAP and sends over
SMTP, so it needs no public address and no mail server of its own.

## Set it up

1. Make an address for the assistant at any provider (for example `lens@yourdomain` or a new Gmail or Fastmail
   account). Most providers give an **app password** for programs like Lens: use that, not your sign-in password.
2. Open **Settings → Assistant email**, enter the address and the password, and turn on **Answer email**.
3. Email the assistant. You show on the settings page under **Wrote to it, not let in**: click **Let in**, and that
   email is answered.

**Check the mailbox** signs in to read and to send, without reading or sending anything, and says what's wrong if it
can't. The mail servers are guessed from the address (Gmail, Outlook.com, iCloud, Fastmail, Yahoo, GMX, Zoho,
mailbox.org and Yandex are known; anything else is tried as `imap.<domain>` and `smtp.<domain>`); set them under
**Mail servers** when the guess is wrong. Mail that arrived before it was turned on isn't answered.

An agent sets it up the same way: `PUT /api/v1/settings/mailbox` with `address`, `password` and `enabled`, then
`POST /api/v1/settings/mailbox/test`; `GET /api/v1/settings/mailbox` says how it's doing and who has written. The
assistant itself can change these settings when you approve it (`change_settings mailbox`).

## Who it answers

Anyone can send email to an address, and anyone can write any address in From, so it answers only mail that is both:

- **from someone let in**: an address in **People it answers** (one click on the settings page), and
- **vouched for by their provider**: the server that received it for the mailbox found that DMARC passed, or DKIM or
  SPF passed for the sender's domain (its `Authentication-Results` header). Gmail, Outlook, iCloud, Fastmail and the
  other big providers add that header to every message.

Nothing else is answered, and nothing tells a stranger the address is read: someone not let in shows on the settings
page, and their last emails wait there until you let them in. Auto-replies, bounces, mailing lists and its own mail
are never answered. Mail that will be answered is marked read in the mailbox; anything else stays unread.

With a mail server of your own: if it doesn't remove `Authentication-Results` headers that arrive with a message, set
**Trust checks by** to the name it signs its own with, so a forged one isn't believed. If it adds none but refuses
forged mail itself, **Whose mail counts → Trust the From address** answers by From alone.

## What it does with an email

Each email thread is a conversation in Chat for the account it answers as (**Answers as**, the admin who turned it
on unless you choose another), so it reads what that account can read. A new email's question is its subject and
what it says; a reply's question is what was written above the quoted part. Its answer lists the excerpts it cites,
each with a link to that moment or page in Lens, quotes your question under it, and is plain text: nothing in it
loads from another server when you read it.

- **Attachments** are imported at once (**What's attached → Imported at once**) into the namespace the assistant
  picks, if that namespace exists; one it would have to create waits for your approval, as does everything with
  **Imported once approved**.
- **A forwarded email** is imported whole, as an email with its attachments. The assistant also reads its text, as
  something someone else wrote: it is marked as data, so instructions written in it aren't followed
  ([third-party content](assistant.md#third-party-content-and-prompt-injection)), and once it has read it, its
  changes wait for your approval, the import too.
- **Approving by email**: reply "yes" (or "no", "yes 2", "yes all") to what it proposes, for entity merges and edits
  and new namespaces; settings, runs and extensions are approved in the web app, linked from the answer.
- `/clear` and `/compact` as an email's text work as in Chat.

## Which namespace

Email to the address with a namespace after a `+` (`lens+pods@yourdomain`, where the provider delivers plus
addresses) goes to that namespace's assistant. Otherwise the first rule in **Where mail goes** that matches decides,
one per line:

```text
from: *@acme.com = acme
subject: invoice; from: billing@x.com, accounts@x.com = finance
subject: newsletter = skip
```

A rule's conditions are on `from`, `to` (To, Cc and the address it arrived at), `subject`, `list` (List-Id) and
`mailbox`, joined by `;`, with patterns joined by `,`. Patterns work as in an email watch's routing rules
([Processing](processing.md#email-imap-calendar-feeds-ical-and-google-calendar)): one without `*` or `?` matches anywhere in the
text. `skip` leaves the email unanswered. When no rule matches, or a rule names a namespace that doesn't exist, Lens
picks the namespace for each question, as in an open chat room ([Chat rooms](chat-rooms.md)).

## How it works

The mailbox is an adapter of the chat-channel layer (`app/domain/channels.py`), next to Matterbridge and Telegram: one
server process at a time reads it, every **Looks for new mail every** seconds (30 by default), and what happens to a
message (the conversation, routing, approvals, files) is the same as in a chat room. The reading is the IMAP code of
email sources (`feeds.py`); answers go out over SMTP with `In-Reply-To`, `References` and
`Auto-Submitted: auto-replied`, so they stay in the thread and other robots don't answer them. Model calls are in the
activity ledger like any chat's.

Refine later: IMAP IDLE instead of looking every few seconds, a copy of answers in Sent, finding the servers of more
providers (autoconfig), letting in whole domains, and answers read aloud as audio attachments.
