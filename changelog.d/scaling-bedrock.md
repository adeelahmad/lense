- **Amazon Bedrock provisioned throughput for auto scaling.** A Bedrock group holds provisioned throughput of a model
  in your AWS account, with no commitment and at the price you give, and Lens's model calls go to it while it runs,
  signed with the provider's AWS key ([Model hosts](docs/scaling.md#model-hosts)).
