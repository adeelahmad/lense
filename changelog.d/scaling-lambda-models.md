- **Lambda as a model host.** A `lambda-models` provider runs a model with vLLM on Lambda GPU instances, which serve
  Lens's model calls while they run. Each instance serves HTTPS with a certificate signed by its group's own
  certificate authority, which Lens makes and keeps sealed, and Lens trusts only that authority for it (the new
  `llm.tls_ca` setting); a firewall ruleset Lens makes once per region opens the port ([Auto scaling](docs/scaling.md)).
