- **Auto scaling's tests hold each provider to its published API.** The RunPod, DigitalOcean, Hetzner Cloud and
  Hugging Face fakes check every request and answer against the provider's own OpenAPI document, and AWS runs on
  moto, a local stand-in for EC2 ([Model hosts](docs/scaling.md#model-hosts)).
