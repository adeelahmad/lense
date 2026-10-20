- **Thumbs up or down on answers.** Under a finished answer in chat, a thumbs up marks it good and a thumbs down bad;
  pressing the one you gave takes it back. The rating is saved on the answer, shown when the conversation opens and
  carried in its JSON export (`lens.rating`), the label training data is built from; it makes no model call. Agents
  rate answers with `PUT /api/v1/chats/{cid}/messages/{mid}/rating` or the MCP tool `rate_answer`, and read it back on
  each message ([Rating answers](docs/assistant.md#rating-answers)). Nothing needs upgrading: older answers have no
  rating.
