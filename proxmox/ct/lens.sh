#!/usr/bin/env bash
# Scripts (this file and install/lens-install.sh) come from the Lens repository; the engine from community-scripts/core.
_CS_DEFAULT_URL="${_CS_DEFAULT_URL:-https://raw.githubusercontent.com/adeelahmad/lense/main/proxmox}"
_cs_boot="${COMMUNITY_SCRIPTS_CORE_DIR:-$(dirname "${BASH_SOURCE[0]}")/../../core}/core/build.func"
source "$_cs_boot" 2>/dev/null || source <(curl -fsSL "${COMMUNITY_SCRIPTS_CORE_URL:-https://raw.githubusercontent.com/community-scripts/core/main}/core/build.func")
# Copyright (c) 2021-2026 community-scripts ORG
# Author: adeelahmad
# License: MIT | https://github.com/community-scripts/ProxmoxVE/raw/main/LICENSE
# Source: https://github.com/adeelahmad/lense

APP="Lens"
var_tags="${var_tags:-archive;media;ai}"
var_cpu="${var_cpu:-4}"
var_ram="${var_ram:-4096}"
var_disk="${var_disk:-20}"
var_os="${var_os:-debian}"
var_version="${var_version:-13}"
var_arm64="${var_arm64:-yes}"
var_unprivileged="${var_unprivileged:-1}"

header_info "$APP"
variables
color
catch_errors

function update_script() {
  header_info
  check_container_storage
  check_container_resources
  if [[ ! -d /opt/lens/.git ]]; then
    msg_error "No ${APP} Installation Found!"
    exit
  fi

  LENS_BRANCH="main"
  SURREALDB_VERSION="3.2.4"

  msg_info "Checking for update: ${APP}"
  CURRENT="$(git -C /opt/lens rev-parse HEAD)"
  LATEST="$(git -C /opt/lens ls-remote origin "refs/heads/${LENS_BRANCH}" | cut -f1)"
  if [[ -z "$LATEST" ]]; then
    msg_error "Could not reach the Lens repository"
    exit
  fi
  if [[ "$CURRENT" == "$LATEST" ]]; then
    msg_ok "No update available: ${APP} (${CURRENT:0:7})"
    exit
  fi
  msg_ok "Update available: ${APP} ${CURRENT:0:7} → ${LATEST:0:7}"

  msg_info "Stopping Services"
  systemctl stop lens-web lens-worker lens-api
  msg_ok "Stopped Services"

  if [[ "$(cat ~/.surrealdb 2>/dev/null)" != "$SURREALDB_VERSION" ]]; then
    msg_info "Updating SurrealDB to ${SURREALDB_VERSION}"
    systemctl stop surrealdb
    curl -fsSL "https://github.com/surrealdb/surrealdb/releases/download/v${SURREALDB_VERSION}/surreal-v${SURREALDB_VERSION}.linux-$(arch_resolve amd64 arm64).tgz" |
      tar -xz -C /usr/local/bin surreal
    echo "${SURREALDB_VERSION}" >~/.surrealdb
    systemctl start surrealdb
    msg_ok "Updated SurrealDB to ${SURREALDB_VERSION}"
  fi

  msg_info "Fetching Lens"
  $STD git -C /opt/lens fetch --depth 1 origin "${LENS_BRANCH}"
  $STD git -C /opt/lens reset --hard FETCH_HEAD
  git -C /opt/lens rev-parse HEAD >~/.lens
  msg_ok "Fetched Lens"

  msg_info "Building Lens API (patience)"
  cd /opt/lens/fastapi_backend
  export UV_PYTHON="3.12"
  $STD uv sync --frozen --no-dev
  msg_ok "Built Lens API"

  msg_info "Building Lens web app (patience)"
  cd /opt/lens/nextjs-frontend
  export NEXT_TELEMETRY_DISABLED=1 CI=true AUTH_SECRET=build-only
  $STD pnpm install --frozen-lockfile
  $STD pnpm build
  unset AUTH_SECRET
  msg_ok "Built Lens web app"

  msg_info "Starting Services"
  systemctl start lens-api lens-worker lens-web
  msg_ok "Started Services"
  msg_ok "Updated successfully!"
  exit
}

start
build_container
description

msg_ok "Completed successfully!\n"
echo -e "${CREATING}${GN}${APP} setup has been successfully initialized!${CL}"
echo -e "${INFO}${YW}Access it using the following URL:${CL}"
echo -e "${GATEWAY}${BGN}http://${IP}:3000${CL}"
echo -e "${INFO}${YW}First admin: run 'lens-setup-code' in the container for the setup code.${CL}"
