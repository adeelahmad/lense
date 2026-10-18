- **Nebula on the primary** (the private network between Lens's machines, in progress). On the primary Nebula runs in
  the optional `mesh` container, which the upgrade agent starts when a node is added: it makes its own key pair, checks
  Nebula's download against the kept checksums and relays the database and the API on the mesh address only. The
  install line that joins a machine and the Settings pages follow in their own pull requests
  ([Nodes and the mesh](docs/mesh.md)).
