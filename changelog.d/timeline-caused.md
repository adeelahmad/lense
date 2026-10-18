An opened timeline item now shows what happened because of it: the automation runs it started (with their status and
a link to the automation) and the approvals those runs asked you for, with Approve and Decline on the ones still
waiting, so a pick is made where it was caused. `GET /api/v1/activities/{id}/caused` and the MCP tool
`activity_caused` read the same for agents, and `approvals` (API, MCP and the assistant) takes the activity that
caused them.
