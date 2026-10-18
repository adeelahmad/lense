- **Worker nodes run jobs** (in progress). On a worker node, `lens node` also runs `lens worker`, against this
  install's database through the mesh, signed in as a user of that one database made for the node (removed with it;
  `SURREAL_AUTH=database`). Its jobs fetch their files from the primary over the mesh, plain, never a vault's or
  another namespace's, and it never gets the archive's secret, so it runs `transcribe` and `diarize` with the models
  on that machine, skipping any other step `LENS_WORKER_STEPS` names. A worker that serves some namespaces takes only
  their jobs, and an auto scaling group on machines elsewhere counts only the jobs a worker node takes, so it never
  launches one for others ([Worker nodes](docs/mesh.md#worker-nodes)).
