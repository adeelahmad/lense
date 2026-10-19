- **A scaling unit that goes leaves the mesh.** When auto scaling lets a unit go, or one fails or never comes up, its
  unspent join token is withdrawn and the machine that joined with it is taken off the mesh, as removing it on the
  Nodes page does: its certificates go on the blocklist and its database user is removed
  ([Machines elsewhere](docs/scaling.md#machines-elsewhere)).
