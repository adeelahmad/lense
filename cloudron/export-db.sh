#!/bin/bash
# Writes a consistent SurrealQL export of the database to /app/data/backup every few hours, so Cloudron's backups
# (which copy /app/data while the app runs) always hold a clean copy next to the live database files.
set -u
umask 077  # the export holds the whole archive

INTERVAL="${LENS_EXPORT_INTERVAL_SECONDS:-21600}"
OUT=/app/data/backup/lens.surql

sleep 600  # let the app settle after a start
while true; do
    if surreal export --endpoint http://127.0.0.1:8001 --user root --pass "${SURREAL_PASS}" \
        --namespace "${SURREAL_NS:-archive}" --database "${SURREAL_DB:-main}" "${OUT}.tmp"; then
        mv "${OUT}.tmp" "${OUT}"
        echo "export-db: wrote ${OUT}"
    else
        rm -f "${OUT}.tmp"
        echo "export-db: export failed, keeping the previous one"
    fi
    sleep "${INTERVAL}"
done
