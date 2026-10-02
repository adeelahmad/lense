#!/bin/sh
# Lens on a QNAP NAS: starts and stops the Lens containers in Container Station's Docker engine.
#   lens.sh start | stop | restart | status | logs [service] | setup-code
# App Center's start/stop buttons and the NAS's boot and shutdown call start and stop.
#
# Everything Lens keeps is in the data folder (Data_Path in /etc/config/qpkg.conf; /share/Container/lens when the
# Container share exists): lens.env (settings and secrets, written on the first start), surrealdb/ (the database) and
# archive/ (uploads, media, derived files). Removing the app leaves it in place, so reinstalling picks up where it was.

CONF=/etc/config/qpkg.conf
QPKG_NAME=Lens
QPKG_ROOT=$(/sbin/getcfg $QPKG_NAME Install_Path -f $CONF)
LENS_VERSION=$(/sbin/getcfg $QPKG_NAME Version -f $CONF)
COMPOSE_FILE="$QPKG_ROOT/docker-compose.yml"
IMAGES_DIR="$QPKG_ROOT/images"
export QNAP_QPKG=$QPKG_NAME

say() { echo "$QPKG_NAME: $*"; }
syslog() {  # the QTS system log (Control Panel > System Logs), where the first-admin setup code is easiest to find
    say "$1"
    /sbin/write_log "[$QPKG_NAME] $1" "${2:-4}" 2>/dev/null || /sbin/log_tool -t "${3:-0}" -a "[$QPKG_NAME] $1" 2>/dev/null || true
}
fail() { syslog "$1" 1 2; exit 1; }

# --- Container Station ------------------------------------------------------------------------------------------
CS_ROOT=$(/sbin/getcfg container-station Install_Path -f $CONF)
export PATH="$CS_ROOT/bin:$CS_ROOT/usr/bin:$PATH"
DOCKER=$(command -v docker || echo "$CS_ROOT/bin/docker")

compose() {
    if "$DOCKER" compose version >/dev/null 2>&1; then
        "$DOCKER" compose -p lens -f "$COMPOSE_FILE" --env-file "$LENS_DATA/lens.env" "$@"
    elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose -p lens -f "$COMPOSE_FILE" --env-file "$LENS_DATA/lens.env" "$@"
    else
        fail "Docker Compose was not found in Container Station ($CS_ROOT). Update Container Station to version 3."
    fi
}

wait_for_docker() {  # at boot, Container Station may still be starting
    i=0
    until "$DOCKER" info >/dev/null 2>&1; do
        i=$((i + 1))
        [ $i -gt 90 ] && fail "Container Station's Docker engine is not running. Start Container Station, then Lens."
        sleep 2
    done
}

# --- The data folder and lens.env -------------------------------------------------------------------------------
data_path() {
    p=$(/sbin/getcfg $QPKG_NAME Data_Path -f $CONF)
    if [ -z "$p" ]; then
        if [ -d /share/Container ]; then p=/share/Container/lens; else p="$(dirname "$QPKG_ROOT")/Lens-data"; fi
        /sbin/setcfg $QPKG_NAME Data_Path "$p" -f $CONF
    fi
    echo "$p"
}
LENS_DATA=$(data_path)

random_hex() { openssl rand -hex "$1" 2>/dev/null || head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }

lan_address() {
    a=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n 1)
    [ -n "$a" ] || a=$(ifconfig 2>/dev/null | sed -n 's/.*inet addr:\([0-9.]*\).*/\1/p' | grep -v '^127\.' | head -n 1)
    [ -n "$a" ] || a=$(hostname)
    echo "$a"
}

write_env() {
    host=$(lan_address)
    hosts=backend
    for h in localhost 127.0.0.1 "$host" "$(hostname)"; do
        case ",$hosts," in *",$h,"*) ;; *) hosts="$hosts,$h" ;; esac
    done
    if [ -d /share/Multimedia ]; then audio=/share/Multimedia/Lens; else audio="$LENS_DATA/media"; fi
    umask 077
    cat > "$LENS_DATA/lens.env" <<EOF
# Lens settings, written on the first start. Edit, then restart Lens in the App Center (or: $QPKG_ROOT/lens.sh restart).
# Keep this file with the data folder. Changing ACCESS_SECRET_KEY signs everyone out; changing ARCHIVE_SECRET_KEY makes
# stored source credentials and model API keys unreadable; SURREAL_PASS can't change once the database exists.
ACCESS_SECRET_KEY=$(random_hex 32)
ARCHIVE_SECRET_KEY=$(random_hex 32)
AUTH_SECRET=$(random_hex 32)
SURREAL_PASS=$(random_hex 16)

# The web app's port on the NAS; keep the one in FRONTEND_URL the same.
LENS_PORT=3000
# The address people open Lens at. Sign-in redirects and email links use it, so change it if you reach Lens by another
# name or IP (a reverse proxy with HTTPS, myQNAPcloud), and add that host name to ARCHIVE_ALLOWED_HOSTS.
FRONTEND_URL=http://$host:3000
ARCHIVE_ALLOWED_HOSTS=$hosts

# Recordings and documents on the NAS: this folder appears in Lens as /audio (read-only), and the setup steps in the
# web app (or Sources, later) can watch it or a folder in it. Only this folder is visible to Lens.
AUDIO_DIR=$audio

# Creates the first admin account in the web app; used only until that account exists.
LENS_SETUP_CODE=$(random_hex 6)

# Mail, for password resets (optional)
MAIL_SERVER=
MAIL_PORT=587
MAIL_USERNAME=
MAIL_PASSWORD=
MAIL_FROM=

# Optional: answer the first-run setup here instead of in the web app (see docs/configuration.md). The admin account
# is created at start from LENS_ADMIN_EMAIL and LENS_ADMIN_PASSWORD; LENS_SETUP_WIZARD=off skips the setup steps.
LENS_ADMIN_EMAIL=
LENS_ADMIN_PASSWORD=
LENS_ADMIN_NAME=
LENS_NAMESPACE=
LENS_LLM_BASE_URL=
LENS_LLM_MODEL=
LENS_LLM_API_KEY=
LENS_LLM_VISION_MODEL=
LENS_SETUP_WIZARD=
EOF
    umask 022
}

env_value() { sed -n "s/^$1=//p" "$LENS_DATA/lens.env" | tail -n 1; }

prepare_data() {
    mkdir -p "$LENS_DATA/surrealdb" "$LENS_DATA/archive" || fail "Could not create the data folder $LENS_DATA."
    [ -f "$LENS_DATA/lens.env" ] || { write_env; FIRST_START=yes; }
    audio=$(env_value AUDIO_DIR)
    [ -d "$audio" ] || mkdir -p "$audio"
    chown 1000:1000 "$LENS_DATA/archive"  # the API and worker run as uid 1000 (user lens) in the image
    port=$(env_value LENS_PORT)
    /sbin/setcfg $QPKG_NAME Web_Port "${port:-3000}" -f $CONF
}

# --- The bundled images -----------------------------------------------------------------------------------------
# images/manifest lists "<image reference> <file>" per line. Each image is loaded once; its file is then deleted to give
# the space back (an upgrade brings new ones). This package's images are tagged qnap-<version>; earlier versions' are
# removed after an upgrade, and other Lens images in Docker (built by hand, say) are left alone.
load_images() {
    [ -f "$IMAGES_DIR/manifest" ] || return 0
    while read -r ref file; do
        [ -n "$ref" ] || continue
        if ! "$DOCKER" image inspect "$ref" >/dev/null 2>&1; then
            [ -f "$IMAGES_DIR/$file" ] || fail "The image $ref is missing and its file is gone. Reinstall Lens from the .qpkg file."
            say "loading $ref (first start after install or upgrade; this takes a few minutes)"
            "$DOCKER" load -i "$IMAGES_DIR/$file" >/dev/null || fail "Could not load $ref into Container Station (is the volume full?)."
        fi
        rm -f "$IMAGES_DIR/$file"
    done < "$IMAGES_DIR/manifest"
    for repo in lens-backend lens-frontend; do
        "$DOCKER" image ls --format '{{.Repository}}:{{.Tag}}' "$repo" 2>/dev/null | grep ":qnap-" | grep -v ":qnap-$LENS_VERSION\$" | while read -r old; do
            "$DOCKER" image rm "$old" >/dev/null 2>&1 || true
        done
    done
}

# --- Commands ---------------------------------------------------------------------------------------------------
export LENS_VERSION LENS_DATA
FIRST_START=""

case "$1" in
  start)
    ENABLED=$(/sbin/getcfg $QPKG_NAME Enable -u -d FALSE -f $CONF)
    if [ "$ENABLED" != "TRUE" ]; then
        say "disabled in the App Center."
        exit 1
    fi
    wait_for_docker
    prepare_data
    load_images
    compose up -d --remove-orphans || fail "The containers did not start. Details: $QPKG_ROOT/lens.sh logs"
    if [ -n "$FIRST_START" ]; then
        syslog "Open $(env_value FRONTEND_URL) and create the first admin account with setup code $(env_value LENS_SETUP_CODE). Settings: $LENS_DATA/lens.env"
    else
        say "running at $(env_value FRONTEND_URL)"
    fi
    ;;

  stop)
    "$DOCKER" info >/dev/null 2>&1 || exit 0  # Container Station already stopped (shutdown): its containers are too
    [ -f "$LENS_DATA/lens.env" ] && compose down --remove-orphans
    ;;

  restart)
    $0 stop
    $0 start
    ;;

  status)
    compose ps
    ;;

  logs)
    shift
    compose logs --tail 200 -f "$@"
    ;;

  setup-code)
    echo "Open $(env_value FRONTEND_URL) and create the first admin with setup code: $(env_value LENS_SETUP_CODE)"
    echo "(It works only until the first account exists.)"
    ;;

  remove)
    # Called by the uninstaller: stop the stack and drop its images. The data folder stays.
    if "$DOCKER" info >/dev/null 2>&1; then
        [ -f "$LENS_DATA/lens.env" ] && compose down --remove-orphans
        for repo in lens-backend lens-frontend; do
            "$DOCKER" image ls --format '{{.Repository}}:{{.Tag}}' "$repo" 2>/dev/null | grep ":qnap-" | while read -r img; do
                "$DOCKER" image rm "$img" >/dev/null 2>&1 || true
            done
        done
    fi
    syslog "Removed. The data folder $LENS_DATA was kept; delete it to remove the database and archive too."
    ;;

  *)
    echo "Usage: $0 {start|stop|restart|status|logs [service]|setup-code}"
    exit 1
    ;;
esac

exit 0
