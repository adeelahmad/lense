- **A busy local model server no longer fails jobs on a dropped connection.** When the LLM server hangs up on a
  request (an Ollama box loading another model resets it), Lens asks once more two seconds later instead of failing
  the job. A reasoning model that spends a short answer's whole length thinking, such as a conversation's title, is
  asked again without the limit instead of leaving the title unwritten
  ([A busy local model server](docs/configuration.md#a-busy-local-model-server)).
