**Reactions and comments on the timeline.** The timeline is a feed everyone in a namespace talks on: an opened item
has a **React** button with a palette (👍 ❤️ 🎉 👀 😂 😮 😢 🙏) and shows everyone's reactions as chips, and its
comments open as threads with **Reply**, where Enter sends. A closed line sums its items' reactions and says "3
comments". A comment on an asset's arrival is a comment on the asset, so the same thread shows on its page. Each
reaction is a `reaction.added` activity and each comment a `comment.added`, so automations can act on them, and
agents react and comment over MCP (`react_to_activity`, `activity_comments`, `comment_on_activity`) and through
`POST /api/v1/activities/{id}/react` and `/activities/{id}/comments`
([Reactions and comments](docs/timeline.md#reactions-and-comments)).
