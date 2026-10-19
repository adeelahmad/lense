- **Circles with other servers.** Once an admin turns it on (Settings → Circles elsewhere) and you make yourself
  findable under Profile, you get a handle like `adeel@lens.example`, and your circles can hold people on other Lens
  or ActivityPub servers by theirs. What you share with those circles reaches them as a signed ActivityPub `Note`, and
  what they send you lands in your timeline, taken only signed, recent and from someone in a circle. Through the API,
  MCP `circles_elsewhere` and `save_circle`, and Profile → Circles
  ([Circles with other servers](docs/federation.md)).
