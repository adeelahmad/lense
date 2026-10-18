- **Hugging Face Inference Endpoints as a model host for auto scaling.** A Hugging Face group serves a model with
  vLLM on hardware picked from Hugging Face's list, which also gives its price, and Lens's model calls go to it
  while it runs, with a key of the unit's own. A unit that never comes up now counts as a failed launch, so three in a
  row pause the group ([Model hosts](docs/scaling.md#model-hosts)).
