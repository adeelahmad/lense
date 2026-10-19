- **SageMaker and Bedrock answers stream.** With an Amazon SageMaker endpoint or Amazon Bedrock as the LLM provider, a
  chat's answer now comes as it is written (InvokeEndpointWithResponseStream and ConverseStream) instead of all at the
  end, and an error the model hits mid-answer says so. Keys also allowed `sagemaker:InvokeEndpointWithResponseStream`
  or `bedrock:InvokeModelWithResponseStream` stream; keys without them get the answer whole, as before ([LLM provider](docs/configuration.md)).
