- **`lens ext`: extensions as files.** `lens ext pull` writes your extensions' manifests into a folder (skills and
  prompt tools as Markdown, the rest as YAML), `lens ext push` saves the changed ones as new versions and the new ones
  as extensions, `lens ext check` checks them without saving, and `lens ext list` lists them (`--json` for agents). It
  works on this install's database, or on any Lens server with `--url` and an API token. A pulled file says its
  version, so a push won't save over changes made in Lens since, and a pull won't overwrite changes made in the file,
  without `--force` ([Manifests as files](docs/assistant.md#manifests-as-files-lens-ext)).
