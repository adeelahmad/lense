#!/usr/bin/env bash

# Copyright (c) 2021-2026 community-scripts ORG
# Author: adeelahmad
# License: MIT | https://github.com/community-scripts/ProxmoxVE/raw/main/LICENSE
# Source: https://github.com/adeelahmad/lense

source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

LENS_REPO="https://github.com/adeelahmad/lense.git"
SURREALDB_VERSION="3.2.4"

msg_info "Installing Dependencies"
# ffmpeg: audio and video; tesseract: text on screen; poppler: PDF fallback (as in fastapi_backend/Dockerfile)
$STD apt install -y \
  git \
  jq \
  ca-certificates \
  ffmpeg \
  tesseract-ocr \
  poppler-utils
# antiword reads legacy .doc files; skip it where the release no longer ships it
if apt-cache show antiword &>/dev/null; then
  $STD apt install -y antiword
fi
msg_ok "Installed Dependencies"

fetch_and_deploy_gh_release "rclone" "rclone/rclone" "binary"
PYTHON_VERSION="3.12" setup_uv
NODE_VERSION="22" NODE_MODULE="pnpm@10.7.1" setup_nodejs

msg_info "Installing SurrealDB ${SURREALDB_VERSION}"
curl -fsSL "https://github.com/surrealdb/surrealdb/releases/download/v${SURREALDB_VERSION}/surreal-v${SURREALDB_VERSION}.linux-$(arch_resolve amd64 arm64).tgz" |
  tar -xz -C /usr/local/bin surreal
chmod +x /usr/local/bin/surreal
echo "${SURREALDB_VERSION}" >~/.surrealdb
mkdir -p /var/lib/surrealdb
msg_ok "Installed SurrealDB ${SURREALDB_VERSION}"

msg_info "Fetching Lens"
# The latest published GitHub release; main until there is one
LENS_REF="$(curl -fsSL https://api.github.com/repos/adeelahmad/lense/releases/latest 2>/dev/null | jq -r '.tag_name // empty' || true)"
LENS_REF="${LENS_REF:-main}"
$STD git clone --depth 1 --branch "${LENS_REF}" "${LENS_REPO}" /opt/lens
git -C /opt/lens rev-parse HEAD >~/.lens
msg_ok "Fetched Lens ${LENS_REF} ($(cut -c1-7 ~/.lens))"

msg_info "Configuring Lens"
LENS_IP="${LOCAL_IP:-$(hostname -I | awk '{print $1}')}"
SURREAL_PASS="$(openssl rand -hex 16)"
mkdir -p /etc/lens /var/lib/lens /var/lib/lens/media
cat <<EOF >/etc/lens/surrealdb.env
SURREAL_USER=root
SURREAL_PASS=${SURREAL_PASS}
SURREAL_BIND=127.0.0.1:8001
SURREAL_LOG=info
EOF
cat <<EOF >/etc/lens/lens.env
# Lens API and worker. Keep the secrets stable: ACCESS_SECRET_KEY signs sessions,
# ARCHIVE_SECRET_KEY encrypts stored source credentials and LLM keys.
ACCESS_SECRET_KEY=$(openssl rand -hex 32)
ARCHIVE_SECRET_KEY=$(openssl rand -hex 32)
ARCHIVE_CONFIG=/etc/lens/archive.yaml
SURREAL_URL=ws://127.0.0.1:8001
SURREAL_USER=root
SURREAL_PASS=${SURREAL_PASS}
RUN_BACKGROUND=false
FRONTEND_URL=http://${LENS_IP}:3000
CORS_ORIGINS=["http://${LENS_IP}:3000"]
# Password reset mail. Unset MAIL_SERVER: reset links are written to the log instead.
# MAIL_SERVER=smtp.example.org
# MAIL_PORT=587
# MAIL_USERNAME=
# MAIL_PASSWORD=
# MAIL_FROM=lens@example.org
EOF
cat <<EOF >/etc/lens/web.env
# Lens web app (Next.js). It proxies /api/v1, /iiif, /embed, /s, /reports and /static to the API.
API_BASE_URL=http://127.0.0.1:8000
AUTH_SECRET=$(openssl rand -base64 32)
AUTH_TRUST_HOST=true
NODE_ENV=production
NEXT_TELEMETRY_DISABLED=1
EOF
cat <<EOF >/etc/lens/archive.yaml
# Lens processing configuration; settings saved in the web app take precedence. See archive.example.yaml.
data_dir: /var/lib/lens
namespaces:
  media:                         # a starting namespace and its folder; add more in the web app
    paths: [/var/lib/lens/media]
server:
  allowed_hosts: [127.0.0.1, localhost]
workers:
  inline: 0                      # lens-worker.service does the work
sources:
  local_roots: [/var/lib/lens/media]
EOF
chmod 600 /etc/lens/*.env
msg_ok "Configured Lens"

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

msg_info "Creating Services"
cat <<EOF >/etc/systemd/system/surrealdb.service
[Unit]
Description=SurrealDB for Lens
After=network.target

[Service]
EnvironmentFile=/etc/lens/surrealdb.env
ExecStart=/usr/local/bin/surreal start surrealkv:/var/lib/surrealdb/lens.db
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat <<EOF >/etc/systemd/system/lens-api.service
[Unit]
Description=Lens API
After=network.target surrealdb.service
Requires=surrealdb.service

[Service]
WorkingDirectory=/opt/lens/fastapi_backend
EnvironmentFile=/etc/lens/lens.env
ExecStart=/opt/lens/fastapi_backend/.venv/bin/fastapi run app/main.py --host 127.0.0.1 --port 8000 --proxy-headers
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat <<EOF >/etc/systemd/system/lens-worker.service
[Unit]
Description=Lens Worker
After=network.target surrealdb.service lens-api.service
Requires=surrealdb.service

[Service]
WorkingDirectory=/opt/lens/fastapi_backend
EnvironmentFile=/etc/lens/lens.env
ExecStart=/opt/lens/fastapi_backend/.venv/bin/lens worker
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat <<EOF >/etc/systemd/system/lens-web.service
[Unit]
Description=Lens Web
After=network.target lens-api.service

[Service]
WorkingDirectory=/opt/lens/nextjs-frontend
EnvironmentFile=/etc/lens/web.env
ExecStart=/opt/lens/nextjs-frontend/node_modules/.bin/next start -H 0.0.0.0 -p 3000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat <<'EOF' >/usr/local/bin/lens-setup-code
#!/usr/bin/env bash
# The first-admin setup code, from the API log (printed only until the first account exists)
journalctl -u lens-api --no-pager | grep -i "setup code" | tail -1
EOF
chmod +x /usr/local/bin/lens-setup-code

systemctl enable -q --now surrealdb
for _ in $(seq 1 30); do
  /usr/local/bin/surreal is-ready --endpoint http://127.0.0.1:8001 &>/dev/null && break
  sleep 1
done
systemctl enable -q --now lens-api lens-worker lens-web
msg_ok "Created Services"

motd_ssh
customize
cleanup_lxc
