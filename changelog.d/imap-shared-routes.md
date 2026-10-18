- **Shared routing rules for an email account.** An IMAP or Gmail account can carry routing rules that every watch
  on it uses, so a shared inbox's policy (support@ to support, newsletters skipped) is written once rather than on
  each mailbox's watch. A watch tries its own rules first, then the account's, then its namespace; with no shared
  rules nothing changes. They're saved with `PUT /api/v1/sources/{id}/routes`, the routes preview takes
  `shared_routes` and says which list decided, and agents read and change every account's and watch's rules with
  the MCP tools `mail_routing` and `set_mail_routing` ([Email](docs/processing.md#email-imap-calendar-feeds-ical-and-google-calendar)).
