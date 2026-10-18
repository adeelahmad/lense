- **Only the person approves what the assistant asks.** Sending mail, adding an event, writing a file and the
  assistant's other proposals are approved only by the person signed in to Lens, or in the chat they were asked in.
  An API key or an app given access can now only decline them (`POST /api/v1/approvals/{id}` answers 403 to an
  approve or a sample), as MCP and the assistant already could, and each approval's `can` says so
  ([Approvals](docs/automations.md#approvals)).
