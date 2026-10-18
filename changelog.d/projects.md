- **Projects.** A project is a goal you work toward with the assistant: a name, the goal in your words, instructions
  and the namespaces it draws on. It comes with a coordinator conversation about the project as a whole, which is told
  about every thread, and its threads are conversations scoped to its namespaces, marked done when finished; your own
  conversations can be brought in or taken out, and deleting a project keeps them. Every answer in a project reads its
  goal and instructions. The coordinator starts threads with a brief (`start_thread`), reads them and marks them done;
  a brief, from it, you or an agent, is answered in the background as you, with your tools, approvals and budgets.
  The Projects page, at the top of Chat's conversation list, starts a project with just a name, starts threads with a
  brief on Enter, shows which are working and marks them done in one click.
  `/api/v1/projects` and the MCP tools `list_projects`, `read_project`, `create_project` and
  `update_project` do it all for agents ([Projects](docs/projects.md)).
