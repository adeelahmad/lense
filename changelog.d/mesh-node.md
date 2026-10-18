- **Joining a node** (in progress). Another machine joins with the install line and `LENS_JOIN` and `LENS_JOIN_TOKEN`:
  it asks nothing, publishes no port, and runs Nebula and `lens node`, which joins once and then follows the primary's
  config, renews its certificate and reports over the mesh. Adding a node token now returns that install line
  ([On a node](docs/mesh.md#on-a-node)).
