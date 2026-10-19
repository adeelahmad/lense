- **A date said becomes a calendar event, once you approve it.** Next to each date said in an asset (its Entities tab,
  under Dates said), **Propose event** makes an event for a Google Calendar connection an admin let add events,
  prefilled from the line: the day said at a time said in it ("at 3" is 15:00) or all day, the line as its title, and
  who it names (for an email, its sender and recipients), never emailed unless you ask. It waits in Approvals, and
  nothing is added or sent until you approve it there, signed in; declining it does nothing. Agents propose through
  `POST /api/v1/dates/propose` and the MCP tool `propose_event`, and can decline but never approve
  ([Propose an event](docs/processing.md#propose-an-event)).
