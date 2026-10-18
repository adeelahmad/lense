- **Automations fire on any entity of a type, or names matching a pattern.** A trigger's where (and an If, and a
  condition node's tree) can test what an activity mentions: `{"op": "mentions", "value": {"type": "PERSON"}}` fires
  on any person, `{"name": "Acme*"}` on names like Acme, with `*` and `?` as wildcards, and `{"id": "lens:entity/9"}`
  on one entity. A processed asset is now tagged with its fifty most mentioned entities, not ten, and every entity tag
  carries its `entityType`. Conditions saved before work as they did
  ([Operators](docs/automations.md#operators)).
