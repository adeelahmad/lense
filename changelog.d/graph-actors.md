**Sources, sensors, people and agents in the graph.** Whoever brought an asset in (the actor of its activity on the
timeline: a storage source, a sensor, a person, an automation, a routine, an import hook, or Lens itself) is now a
Source node in the graph with an ADDED link to the asset, so `(Source)-[:ADDED]->(Recording)-[:MENTIONS]->(Entity)`
says where Lens learns about each person, place or organisation. The canvas draws sources linked to what their assets
mention, the explorer lists an asset's source among its parents, and the Timeline page's Graph button opens the canvas
on the sources alone ([Actors in the graph](docs/timeline.md#actors-in-the-graph)).
