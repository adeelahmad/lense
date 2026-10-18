- **Agents manage the mesh's machines** (in progress, as the mesh is). Over MCP and in the assistant, an admin's agent
  reads the mesh (`mesh`: its CA, this machine's address, every machine and how it's doing, the join tokens not used
  yet, never their secrets) and adds a machine (`add_mesh_node`, which answers with the install line), renames one or
  changes its namespaces, takes one off and brings it back, and withdraws a join token. In the assistant, adding a
  machine and taking one off always wait for your yes, and the install line is shown to you once on the approval
  card, never kept or sent to the model; the rest happens at once. Over MCP every change needs an admin's
  write-scope token and is audited ([Agents](docs/mesh.md#agents)).
