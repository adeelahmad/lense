# Updates and backups

Lens tells admins when a new release is out, backs the database up, and installs the release from the web app, so
upgrading never needs a terminal. Status: **built** (the check, backups and upgrades from the API and the agent);
the web app's banner and Settings page come in their own change.

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
   new version. The database upgrades itself on start ([Database](database.md#upgrades)).

The agent installs only a release tag (`vX.Y.Z`) or the branch the install already follows, whoever writes the
request, and the API refuses a release older than the one running (restore a backup instead).

With no agent (Lens wasn't installed with install.sh, the machine has neither systemd nor launchd, or install.sh ran
with `LENS_UPGRADE_AGENT=0`), the API gives the one line to run on the machine instead (`manual`).

`updates.auto_install: true` installs a new release on its own as soon as the check sees it, once per release. It is
off by default: what Lens runs changes only when an admin says so.

## Backups

* `GET /api/v1/admin/backups`: the backups in `<data_dir>/backups`, newest first.
* `POST /api/v1/admin/backups`: back up now (`manual-<time>`), as `lens backup` does.
* `GET /api/v1/admin/backups/{name}`: download one (SurrealDB exports; an embedded database's folder is copied from
  the data folder instead).
* `DELETE /api/v1/admin/backups/{name}`.

Restoring one is described in [Database](database.md#backups).
