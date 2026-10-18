- **Leave words out of a search.** `budget -draft` or `budget NOT draft` finds the lines about the budget that don't
  mention a draft (or drafts), and `-"first draft"` leaves out a phrase; a typed AND changes nothing, as every word is
  needed anyway. It works in the web app, the API, MCP and the assistant's search, and the "?" help shows it.
