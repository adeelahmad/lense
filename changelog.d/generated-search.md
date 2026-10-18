- **Search finds what a pipeline's model step wrote.** The words of the output an `llm` step saves on an asset (and of
  a workflow's Save output node after an LLM prompt node) are found by search, the API's `GET /search` and MCP's
  `search`, as `source: generated` with the step's name in `output_label`; the web app shows "Written by <step>".
  Running the step again replaces them and deleting the asset removes them. A step keeps its output out of search with
  *Find what it writes in search* unticked (`searchable: false`); it is on otherwise. The upgrade step
  `generated-search` makes outputs a model wrote before searchable too
  ([Pipelines and templates](docs/processing.md#pipelines-and-templates)).
