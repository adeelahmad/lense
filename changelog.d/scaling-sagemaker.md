- **Amazon SageMaker as a model host for auto scaling.** A SageMaker group serves a model with AWS's vLLM container on
  a real-time endpoint in your AWS account, priced from the AWS Price List, and Lens's model calls go to it while it
  runs, signed with the provider's AWS key. A SageMaker endpoint can also be set as the LLM provider by hand, with an
  AWS access key as its key ([Model hosts](docs/scaling.md#model-hosts)).
