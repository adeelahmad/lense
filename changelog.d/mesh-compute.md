- **Compute nodes run the assistant's code** (in progress). A compute node's install line now has
  `LENS_NODE_SANDBOX=1`, which starts an OpenShell gateway on that machine. `lens node` hands Lens the gateway's
  client certificate once, kept sealed, and reports whether the gateway answers. Lens reaches the gateway over the
  mesh only, with mutual TLS, so `sandbox_nodes` lists the node and `run_python` and `run_bash` can run there
  ([Compute nodes](docs/mesh.md#compute-nodes)).
