- **Agents find, install and update marketplace plugins.** `find_plugins` and `install_plugin` (over MCP, and in
  chat, where installing waits for your yes) look across every marketplace added and install a plugin as your own
  extension. A plugin you installed now says which version you have, and its button reads **Update to** the version
  its marketplace lists when that's another; `GET /api/v1/marketplaces/plugins` finds plugins across marketplaces
  (`?updates=true` for those with another version)
  ([Skills and plugins from marketplaces](docs/assistant.md#skills-and-plugins-from-marketplaces)).
