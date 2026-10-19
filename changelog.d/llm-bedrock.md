- **Amazon Bedrock as the LLM provider.** Set the address to `https://bedrock-runtime.<region>.amazonaws.com`, the
  model to a Bedrock model id or inference profile, and the key to an AWS access key: Lens's chat, tool calls, images
  and summaries go to Bedrock's Converse API, signed with the key ([LLM provider](docs/configuration.md)).
