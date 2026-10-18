**Mute a source on the timeline.** An opened item has **Mute Work Gmail**: everything from that source or person
leaves your timeline, with Undo, and a Muted row above the list lifts each one. Muting hides, it never deletes: the
activities stay, still trigger automations and still show to everyone else, and `hidden=true` shows them to you.
Agents mute a source or a kind, for good or until a time, with `POST /api/v1/activities/mute`, the MCP
`mute_activities` tool and the assistant, which asks first after it has read outside text
([Muting](docs/timeline.md#muting)).
