#!/usr/bin/env bash
# Prepares an Ubuntu (22.04 or newer, x86_64) machine with a GitHub Actions runner for Lens CI: Docker with buildx
# (the SurrealDB server tests and the Cloudron, QNAP and Synology image builds), and the tools the backend tests use
# (ffmpeg, tesseract, poppler, LibreOffice Writer), so the CI's apt step finds them and needs no sudo.
#
#   sudo bash setup-ubuntu-runner.sh [RUNNER_USER]
#
# RUNNER_USER is the account the runner runs as (default: the user who ran sudo). It is added to the docker group,
# and the runner's service, if it has one, is restarted so jobs get that group. Safe to run again.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run it with sudo: sudo bash $0 [RUNNER_USER]" >&2; exit 1; }
RUNNER_USER=${1:-${SUDO_USER:-}}
[[ -n $RUNNER_USER && $RUNNER_USER != root ]] || { echo "name the runner's user: sudo bash $0 <user>" >&2; exit 1; }
id "$RUNNER_USER" >/dev/null 2>&1 || { echo "no user $RUNNER_USER" >&2; exit 1; }
# shellcheck disable=SC1091
. /etc/os-release
[[ $ID == ubuntu ]] || echo "warning: written for Ubuntu, this is $PRETTY_NAME" >&2

export DEBIAN_FRONTEND=noninteractive
echo "==> Packages for the jobs"
apt-get update -q
apt-get install -y -q --no-install-recommends \
    ca-certificates curl git gnupg jq unzip zip zstd xz-utils rsync build-essential python3 python3-venv \
    ffmpeg tesseract-ocr poppler-utils libreoffice-writer-nogui

echo "==> Docker"
if ! docker buildx version >/dev/null 2>&1; then
    # Docker's own packages: buildx and compose come with them
    apt-get remove -y -q docker.io docker-doc docker-compose podman-docker containerd runc >/dev/null 2>&1 || true
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
usermod -aG docker "$RUNNER_USER"

echo "==> Runner service"
restarted=""
for unit in $(systemctl list-units --all --plain --no-legend 'actions.runner.*' | awk '{print $1}'); do
    systemctl restart "$unit" && restarted="$restarted $unit"
done

echo
echo "Done: Docker $(docker version --format '{{.Server.Version}}'), $(ffmpeg -version | head -n1 | cut -d' ' -f1-3)," \
    "tesseract $(tesseract --version 2>&1 | head -n1 | cut -d' ' -f2), $(soffice --version | head -n1)."
if [[ -n $restarted ]]; then
    echo "Restarted$restarted, so jobs run with the docker group."
else
    echo "$RUNNER_USER is in the docker group now: restart the runner (stop ./run.sh and start it again, from a new"
    echo "login shell) so its jobs can use Docker."
fi
