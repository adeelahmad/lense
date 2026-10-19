- **Model calls spread across a group's units.** While a model group serves Lens's model calls, every running unit of
  it takes them in turn, not only the one Settings › LLM provider shows, so scaling a group out adds capacity. A
  group's units now share one key, sealed on the group, and the scaler keeps the new `llm.spread` setting at their
  addresses; a unit that can't be reached hands its call to the next ([Auto scaling](docs/scaling.md)).
