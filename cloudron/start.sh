#!/bin/bash
# Cloudron entry point: prepares /app/data, maps Cloudron's environment onto Lens's, then hands over to supervisord
# (SurrealDB, API, worker, web app, database export).
set -eu

mkdir -p /app/data/surreal /app/data/archive /app/data/media /app/data/backup /app/data/home /run/lens/next-cache

# Secrets made once on first start and kept with the app's data (and its backups)
if [ ! -f /app/data/secrets.env ]; then
    echo "==> First start: creating secrets"
    {
        echo "ACCESS_SECRET_KEY=$(openssl rand -hex 32)"
        echo "ARCHIVE_SECRET_KEY=$(openssl rand -hex 32)"
        echo "AUTH_SECRET=$(openssl rand -hex 32)"
        echo "SURREAL_PASS=$(openssl rand -hex 16)"
        echo "LENS_SETUP_CODE=$(openssl rand -hex 6)"
    } > /app/data/secrets.env
    chmod 600 /app/data/secrets.env
fi
set -a
# shellcheck disable=SC1091
source /app/data/secrets.env
set +a

# Processing configuration: written once, then the admin's to edit (Cloudron's file manager or web terminal)
[ -f /app/data/archive.yaml ] || cp /app/code/cloudron/archive.yaml /app/data/archive.yaml

export HOME=/app/data/home XDG_CACHE_HOME=/app/data/home/.cache
export SURREAL_URL=ws://127.0.0.1:8001 SURREAL_USER=root
export ARCHIVE_CONFIG=/app/data/archive.yaml
export RUN_BACKGROUND=false
export FRONTEND_URL="${CLOUDRON_APP_ORIGIN}"
export CORS_ORIGINS="[\"${CLOUDRON_APP_ORIGIN}\"]"

# Mail for password resets and access requests (Cloudron's sendmail addon: plain SMTP inside the server)
if [ -n "${CLOUDRON_MAIL_SMTP_SERVER:-}" ]; then
    export MAIL_SERVER="${CLOUDRON_MAIL_SMTP_SERVER}" MAIL_PORT="${CLOUDRON_MAIL_SMTP_PORT}" \
        MAIL_USERNAME="${CLOUDRON_MAIL_SMTP_USERNAME}" MAIL_PASSWORD="${CLOUDRON_MAIL_SMTP_PASSWORD}" \
        MAIL_FROM="${CLOUDRON_MAIL_FROM}" MAIL_FROM_NAME="${CLOUDRON_MAIL_FROM_DISPLAY_NAME:-Lens}" \
        MAIL_STARTTLS=False MAIL_SSL_TLS=False USE_CREDENTIALS=True VALIDATE_CERTS=False
fi

# The web app: the Next.js server reaches the API on localhost
export API_BASE_URL=http://127.0.0.1:8000 AUTH_URL="${CLOUDRON_APP_ORIGIN}" AUTH_TRUST_HOST=true NODE_ENV=production

chown -R cloudron:cloudron /app/data /run/lens

echo "==> First-admin setup code (only needed until the first account exists): ${LENS_SETUP_CODE}"
exec /usr/bin/supervisord --configuration /app/code/cloudron/supervisord.conf --nodaemon
