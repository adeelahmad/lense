# Nodes and the mesh

Status: **in progress**. Built: Lens as the mesh's certificate authority, join tokens, joining without sending the
token, each machine's Nebula config, a node's renewals and reports, removing a node, the `mesh` settings, the API
under `/api/v1/mesh`, Nebula running on the primary in the `mesh` container, relaying the database and the API, and
`lens node` with the install line that joins another machine. Coming in their own pull requests: worker nodes running
jobs, Settings › Network and Nodes in the web app, and running code on compute nodes.

Lens runs on one machine until you add another: a Mac with a GPU to transcribe on, a box to run the assistant's code
on, a server with more disk. Those machines talk to this install over a private network of Lens's own, a
[Nebula](https://github.com/slackhq/nebula) mesh (MIT), never over the LAN or the internet in the clear: every packet
between them is encrypted and signed with certificates Lens issued, and Nebula's firewall lets through only what each
role needs. A single-machine install never turns it on.

**Off until an admin adds a node.** Adding a node (Settings › Nodes, or `POST /api/v1/mesh/tokens`) makes the mesh's
certificate authority the first time and turns `mesh.enabled` on. Nothing goes in `.env`; nothing is asked at
install.

## Roles

| Role | What it does | What it reaches on the primary |
| --- | --- | --- |
| **primary** | This install: the lighthouse other machines find each other through, the relay for machines that can't reach each other directly, and the certificate authority | |
| **worker** | Runs jobs (transcription, OCR, the model steps it can) against this install's database, as `lens worker` does on this machine | the database port (8000) and the API (8080) |
| **compute** | Runs code in sandboxes for the assistant. It gets nothing of Lens's: no database, no keys | the API (8080) |

A worker node needs this install's database to be a SurrealDB server, which Docker installs have (`SURREAL_URL`);
on an install with the embedded database only compute nodes can be added. A node may serve some namespaces only
(`namespaces` on its token, changed later on the node): its certificate carries them as Nebula groups
(`ns-<namespace>`) for the firewall, and its workers take only those namespaces' jobs.

## Joining

An admin makes a join token for a role, shown once. It can be used once (or `uses` times) within `hours` (a day by
default), and withdrawn before that. The machine joins at this install's address with:

```sh
curl -fsSL https://raw.githubusercontent.com/adeelahmad/lense/main/install.sh | LENS_JOIN=http://lens.home:3000 LENS_JOIN_TOKEN=lensjoin1.… sh
```

`LENS_JOIN` is this install's address, the web app's (the one Settings › Nodes shows); with it the install line makes
the machine a node (below) instead of installing Lens. `LENS_NODE_NAME` names it (default: the machine's own name, or
for a unit an [auto scaling group](scaling.md) starts, `LENS_SCALING_UNIT`, which is kept with `LENS_WORKER_STEPS` for
the node's worker). `POST /api/v1/mesh/tokens` answers with the whole line (`install_line`).

The token never crosses the wire. The machine makes its own Nebula key pair (`nebula-cert keygen`; the private key
never leaves it) and sends `POST /api/v1/mesh/join` with the token's id, a random nonce, its name, its public key and
a proof: `HMAC-SHA256(secret, "lens-mesh-join\n<id>\n<nonce>\n<name>\n<public key>")`. Lens checks the proof in
constant time, spends the token, gives the machine the next address in `mesh.network`, signs a certificate for its
public key naming its role and namespaces as groups, and answers with its certificate, its whole Nebula config and its
credentials, sealed with AES-256-GCM under a key derived from the token's secret (HKDF-SHA256 with the nonce as salt).
So a join over plain `http://` on the LAN gives an eavesdropper nothing, and a machine in the middle can neither join
nor read the answer. Wrong proofs count towards a throttle per address (8 in 15 minutes).

The answer holds: the node's id, name, role, address and groups; the primary's mesh address and ports; the config
(below); a **node token** (`lensnode1.<id>.<secret>`, kept on the node only, as a hash on the primary) for the node's
own calls; and an **agent key** the primary signs its calls to the node with.

## After joining

Over the mesh, with its node token (`Authorization: Bearer lensnode1.…`):

| Call | |
| --- | --- |
| `GET /api/v1/mesh/node/config` | its Nebula config as it is now, and a `version` that changes when the config does (the blocklist grew, the primary's addresses changed): the node reloads Nebula when it sees a new one |
| `POST /api/v1/mesh/node/renew` | a new certificate for its public key (the same or a new one), with the groups its row has now. A node renews at a third of `mesh.cert_days` before its certificate runs out |
| `POST /api/v1/mesh/node/beat` | its report, every minute: whether Nebula runs, its peers, its machine, the sandboxes it can run, Nebula's last lines. Shown on the Nodes page |

A removed node is refused on all three. Nodes make these calls at the primary's mesh address (`10.73.0.1:8080` by
default), which isn't a host name the web app is served at: the API admits it for these paths alone, while the mesh
is on, and each call still needs its node token.

## On a node

The install line with `LENS_JOIN` makes the machine a node, in `~/lens-node` (`LENS_DIR` for another folder), with
Docker, which it installs as for Lens. It asks nothing, publishes no port and starts two containers
(`docker-compose.node.yml`, in the smaller `lean` image by default):

- **`mesh`**, the same container as on the primary (`mesh/sidecar.py`): it makes the node's key pair and runs Nebula
  with the config it's given. Nothing listens on the node but Nebula, on a port it picks.
- **`node`** runs `lens node`, in the `mesh` container's network, with the folder the two share. It needs no database
  and no settings. It turns the mesh on in that folder, so the container makes its key pair; joins with the token (as
  above); keeps its node token and agent key in its own volume (`node.json`, readable only by it); and writes the
  config for the container. Then, every minute, over the mesh only: it fetches its config (a removed machine's
  certificate on the blocklist reaches it within the minute), renews its certificate when a third of its life is
  left or the container's key changed, and sends its report.

The install line waits until the node has joined and says its mesh address. A token that was used, expired or
withdrawn isn't tried again: the line says why, and is run again with a new token. The token is in the first start's
environment only, never in `.env`, and isn't needed again: running the line again updates the node, which stays the
same node. A node that's removed keeps asking, so an undo in Settings › Nodes brings it back; `docker compose -f
docker-compose.node.yml exec node lens node status` says what it is on the mesh, without its credentials.

## On this machine

Lens's server can't create network devices, and nothing in Lens but Nebula needs to, so on the primary Nebula runs in
a container of its own, `mesh` (`mesh/sidecar.py`, in the plain Python image). It's an optional service like the
browser or monitoring: adding a node asks the machine's upgrade agent to start it, and turning the mesh off
(`mesh.enabled`) stops it. Without an upgrade agent, start it with the line Settings › Network shows:

```sh
docker compose -f docker-compose.prod.yml --profile mesh up -d
```

- **Its own key.** The container makes this machine's Nebula key pair the first time and keeps the private key in its
  own volume. It shares a folder with Lens's server (`LENS_MESH_DIR`, `/mesh`): it puts the public key there, Lens
  signs it and writes the config back (`lens.json`), and the container adds the private key and runs Nebula. Lens
  signs it again when a third of its life is left, or when the container's key changes.
- **Checked before it runs.** It fetches Nebula's release for the machine on first use, checked against the same
  checksums Lens keeps, and runs each new config past `nebula -test` first. A change Nebula can take while running
  (a removed node's certificate on the blocklist, the firewall) is reloaded without dropping tunnels; a new network
  device or port restarts it.
- **Relays.** It listens on this machine's mesh address only, and passes port 8000 to the database and port 8080 to
  the API: what a worker node and every node reach here, as Nebula's firewall allows (below).
- **The port.** Other machines reach it on UDP 4242, which Docker publishes (`LENS_MESH_PORT` in `.env` publishes
  another; `mesh.port` is the port nodes are told to use, so change both, or set `mesh.hosts` to `host:port` when a
  router forwards another port to it).
- **What it says.** It writes how it runs (`status.json`): Nebula on or not, since when, the relays and Nebula's last
  lines. `GET /api/v1/mesh` shows it under `service`, and the primary's own row in `/api/v1/mesh/nodes` carries it as
  a node's report does.

It holds `NET_ADMIN` and `/dev/net/tun` and nothing else: no other capability, a read-only image, 256 MB of memory,
none of Lens's data or keys but its own key pair and the config.

## The config

Each machine's config is rendered by Lens, from the settings and the node's row; the node adds only its private key.

- `pki`: the CA certificate, the machine's certificate, and the **blocklist**: the fingerprints of every certificate a
  removed node was given. Nebula drops a blocked peer's tunnels and refuses new ones (`disconnect_invalid`).
- `lighthouse` and `static_host_map`: the primary (the network's first address) is the lighthouse; nodes find it at
  `mesh.hosts` on `mesh.port`, or, when that's empty, at the address they joined this install at.
- `relay`: the primary relays for nodes that can't reach each other directly (`mesh.relay`).
- `firewall`: outbound anything; inbound ICMP, and on the primary the database port (8000) to the `lens-worker` group
  and the API port (8080) to `lens-node`; on a node its agent port (7443) to `lens-primary` only. Everything else is
  dropped.
- `tun`: the `lens-mesh` interface, MTU `mesh.mtu`. `cipher`: `mesh.cipher`, the same on every machine.

Nebula's programs come from Nebula's release for the machine (Linux amd64, arm64, ARMv7 and ARMv6 for a Raspberry
Pi; macOS), pinned to one version with its checksum kept in Lens, fetched into `<data_dir>/bin` on first use; a
`nebula` and `nebula-cert` on `PATH` or in `LENS_NEBULA_DIR` are used instead.

## Settings

Everything is in **Settings › Network** (`mesh`), editable in the app with defaults:

| Setting | Default | |
| --- | --- | --- |
| `enabled` | off | on when a node is added; off stops the mesh |
| `network` | `10.73.0.0/16` | the mesh's addresses, a private IPv4 range from /8 to /28; the primary is its first. It can't change once machines have addresses in it |
| `port` | `4242` | the UDP port nodes reach the primary at: the one Docker publishes (`LENS_MESH_PORT`), unless a router forwards another |
| `hosts` | none | where nodes reach that port from outside the mesh (`host` or `host:port`, up to 8). Empty: the address each node joined this install at |
| `cipher` | `aes` | or `chachapoly` (faster on machines without AES hardware, such as some Raspberry Pis); the same on every machine |
| `cert_days` | 30 | how long a machine's certificate lasts; it renews at two thirds |
| `relay` | on | the primary relays for machines that can't reach each other directly |
| `mtu` | 1300 | the mesh interface's MTU |
| `log_level` | info | Nebula's log level |

**Nodes** (`/api/v1/mesh/nodes`): every machine, this one first, with its role, address, certificate and when it runs
out, last report, and the job workers running on it. `PATCH` renames a node or changes its namespaces; `DELETE`
removes it (undo with `POST /nodes/{id}/restore` while its certificate is good). Tokens: `GET`, `POST`, `DELETE
/api/v1/mesh/tokens`.

## What keeps it safe

- Nothing listens until an admin adds a node, and single-machine installs never do.
- On the primary only the `mesh` container holds `NET_ADMIN`; it makes its own key pair, runs only Nebula's release with
  the checksum kept in Lens, and its relays listen on the mesh address alone.
- Only a machine with a token an admin made joins; the token is spent on use, expires, and never crosses the wire.
- A machine's certificate names only its own address, role and namespaces; it can't widen them by renewing.
- Nebula's firewall, rendered by Lens, opens only what each role needs. Lens itself never trusts the mesh's address:
  a worker node signs in to the database with its credential, a node's calls carry its node token, and the primary's
  calls to a node carry its agent key.
- Removing a node blocks every certificate it was given, on every machine, and its credentials stop at once.
- The CA's key and the tokens' secrets are sealed in the database like any setting's secret (`ARCHIVE_SECRET_KEY` or
  `secret.key`); node tokens are kept only as hashes.
- Nebula's download is pinned to one version with its checksum kept in Lens, unlike programs fetched from a "latest"
  release.

What it doesn't do: a worker node holds the database credential, so it is a trusted peer as a worker on this machine
is; a compute node is not, and gets no database. The lighthouse must be reachable on UDP `mesh.port` from the nodes
(a Raspberry Pi behind a carrier's NAT can be a node, not the primary a node reaches directly, unless `mesh.hosts`
names an address that forwards to it). Nebula's own isolation is upstream's.

## Refine later

- The upgrade agent on nodes: today a node is updated by running its install line again.

- A second transport for networks that block UDP.
- Rotating the CA.
- Compute nodes running the assistant's sandboxes through the mesh, and their load shown on the Nodes page.
