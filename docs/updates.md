# Updates and backups

Lens tells admins when a new release is out, backs the database up, and installs the release from the web app, so
upgrading never needs a terminal. Status: **built**: the check, backups, one-click upgrades with their progress and a
rollback when the new version doesn't start, in Settings → Updates and a banner for admins, and
[setting the machine up again](#setting-the-machine-up-again) from Settings.

## The check

Once a day (`updates.check_hours`) one process asks GitHub for the latest release of `updates.repo` (default
`adeelahmad/lense`) and keeps the answer, so every process and the web app see the same thing. That one request is all
it sends: no install id and nothing about this install. Turn it off with `updates.check: false` in Settings; admins can
still check by hand (`POST /api/v1/admin/updates/check`).

`GET /api/v1/admin/updates` says which version is running, the latest release and its notes, whether it is newer, and
how the last upgrade went.

## Being told

When a check finds a release newer than the one running, Lens says so once per release:

* **by email** to every admin (`updates.email`, on by default; needs an email server in Settings › Email);
* **to notification targets** (webhooks, Matterbridge, Slack, Discord) whose namespace owners chose the
  **A new Lens release is out** event (`lens.release`, [Notifications](notifications.md));
* to automations, as a `lens.release` activity ([Automations](automations.md#lens-itself-as-activities));
* in the server log.

## Upgrading

Lens can't replace its own containers (or its microsandbox) from inside them, so a small agent on the machine does it.
install.sh sets it up:

* **Linux** (systemd): `lens-upgrade.path` watches `<install folder>/.lens-control/inbox` and starts
  `lens-upgrade.service`, which runs as the user who installed Lens (as root only when Docker needs root there).
* **Mac** (launchd): `~/Library/LaunchAgents/dev.lens.upgrade.plist` does the same.

The containers see the control folder as `/control` (`LENS_CONTROL_DIR`). `POST /api/v1/admin/updates/upgrade`:

1. backs the database up into `<data_dir>/backups` (`before-upgrade-<time>`, the newest 3 are kept), and stops there
   if that fails;
2. drops a request for the release in the inbox;
3. the agent (`packaging/host/lens-upgrade.sh`) runs install.sh again for that release, exactly as running the install
   line by hand does: the secrets, the database, the archive, the port and the address are kept;
4. it writes `upgrade-status` and `upgrade.log` next to the inbox, which the API reads back while Lens restarts on the
   new version, with the step it is on (download, build, start, check), which Settings → Updates shows. The database
   upgrades itself on start ([Database](database.md#upgrades)).

### When the new version doesn't start

If install.sh fails for the new version (it didn't build, or didn't answer in time), the agent goes back by itself:

1. it checks out the code that ran before and runs install.sh on it again (`LENS_KEEP_CODE=1`: no download), so the
   previous version runs again (`rolling-back`, then `rolled-back`);
2. it leaves `inbox/restore` naming the backup taken first. As the previous version starts, it looks at the database:
   if the new version had changed its shape (ran upgrade steps the previous version doesn't know), the backup is put
   back; otherwise the database is kept as it is, so nothing written since is lost. What it did is shown with the run.

Only the API restores the backup; a worker running meanwhile may write to the database as it is put back, so its work
from those seconds can be lost. If going back fails too, the run ends `failed` and says to run the install line.

The agent installs only a release tag (`vX.Y.Z`) or the branch the install already follows, whoever writes the
request, and the API refuses a release older than the one running (restore a backup instead).

The same agent turns Lens's [optional containers](assistant.md#optional-containers) on and off: a request with
`action=services`, a `profile` and `turn=on` or `off` (in the same inbox file, the one every agent watches) adds or
removes that profile in `COMPOSE_PROFILES` in `.env` and starts or stops its containers, and nothing else. The
profiles are fixed in the agent: `mcp` (the MCP runner), `browser` (the browser and its proxy), `sandbox` (the
[code sandbox](assistant.md#the-gateway)'s gateway, its certificates and the client's key), `monitoring`
([Charts and history](prometheus.md#prometheus-and-grafana-ready-made): Prometheus, node-exporter and Grafana) and
`mesh` ([Nodes and the mesh](mesh.md#on-this-machine): Nebula on the primary); it refuses any other. It reports in `services-status` and `services.log`. A request from an older Lens, with
`monitoring=on` or `off` and no profile, still works.

With no agent (Lens wasn't installed with install.sh, the machine has neither systemd nor launchd, or install.sh ran
with `LENS_UPGRADE_AGENT=0`), the API gives the one line to run on the machine instead (`manual`).

`updates.auto_install: true` installs a new release on its own as soon as the check sees it, once per release. It is
off by default: what Lens runs changes only when an admin says so.

## Setting the machine up again

Some choices are made when Lens is installed, as settings on the install line. Three of them can be changed later from
the web app, with no terminal: the agent runs install.sh again for the version running (a request with an empty
`ref`), changing only what the request says, and Lens restarts.

| In Settings | On the install line | In the request |
| --- | --- | --- |
| **Access & embedding › Reachable from other devices on this network** | `LENS_PUBLIC=1` (or `0`: this machine only) | `public=1` or `0` |
| **Components › Edition**: Full (LibreOffice and Chromium, to read Office files, web pages and emails) or Lean | `LENS_TARGET=full` or `lean` | `target=full` or `lean` |
| **HTTPS › Open the HTTPS ports** ([HTTPS](https.md)) | `LENS_HTTPS=1` | `https=1` or `0` |

Changing one says what will happen (Lens restarts; opening it to the network lets other devices reach its sign-in
page; with Docker a new edition is built, which can take a few minutes), then one button applies it. Without an
agent, the same place shows the install line to run on the machine. install.sh keeps each choice in `.env`
(`LENS_BIND`, `LENS_TARGET`, `LENS_HTTPS`), so upgrades keep them; what a request leaves out stays as `.env` has it.
The agent takes nothing but `0` or `1`, and `full` or `lean`.

Lens reads how it runs from what it was started with: `docker-compose.prod.yml` and a microsandbox pass on
`LENS_TARGET` and `LENS_BIND` (`0.0.0.0` or the machine's own address: the network; `127.0.0.1`: this machine only).
Started some other way, it says it doesn't know, rather than guessing.

For agents: `GET /api/v1/admin/install` says `public` and `target` (or null), whether there's an agent, the install
line for each choice (`manual`) and how the last change went (`reconfigure`). `PUT /api/v1/admin/install` with
`{"public": true}`, `{"target": "lean"}` or both asks for the change (202, audited as `lens.install`); what is so
already is left alone, and with nothing left to change nothing restarts (200). Without an agent it answers 409 with
the line to run. The MCP tools `install_settings` and `set_install_settings` do the same (admins, and a write-scope
token to change them).

### Refine later

- Windows installs have no upgrade agent, and the line Lens shows to run there is the one for Linux and Macs
  (`curl … | sh`), for upgrades, the HTTPS ports and these choices alike.
- Installs not started by install.sh, `docker-compose.prod.yml` or a microsandbox (Cloudron, the NAS packages,
  Proxmox) show whether the network reaches Lens and its edition as not known.

## Backups

* `GET /api/v1/admin/backups`: the backups in `<data_dir>/backups`, newest first.
* `POST /api/v1/admin/backups`: back up now (`manual-<time>`), as `lens backup` does.
* `GET /api/v1/admin/backups/{name}`: download one (SurrealDB exports; an embedded database's folder is copied from
  the data folder instead).
* `DELETE /api/v1/admin/backups/{name}`.

Restoring one is described in [Database](database.md#backups).
