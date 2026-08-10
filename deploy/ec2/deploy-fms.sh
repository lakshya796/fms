#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/opt/phloz/fms"
REPOSITORY_URL="${FMS_REPOSITORY_URL:-https://github.com/lakshya796/fms.git}"
BRANCH="${FMS_BRANCH:-main}"
RELEASE="${APP_ROOT}/releases/$(date +%Y%m%d%H%M%S)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sudo mkdir -p "${APP_ROOT}/releases" "${APP_ROOT}/shared"
sudo chown -R ec2-user:ec2-user "${APP_ROOT}"

SOURCE_DIR="$(mktemp -d)"
trap 'rm -rf "$SOURCE_DIR"' EXIT
git clone --depth 1 --branch "$BRANCH" "$REPOSITORY_URL" "$SOURCE_DIR"

mkdir -p "$RELEASE"
cp -a "$SOURCE_DIR/backend/." "$RELEASE/"
ln -s "${APP_ROOT}/shared/db.sqlite3" "$RELEASE/db.sqlite3"

if [ ! -d "${APP_ROOT}/venv" ]; then
    python3 -m venv "${APP_ROOT}/venv"
fi
"${APP_ROOT}/venv/bin/pip" install --no-cache-dir -r "$RELEASE/requirements.txt"

if [ ! -f "${APP_ROOT}/shared/fms.env" ]; then
    secret=$("${APP_ROOT}/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(64))')
    umask 077
    printf 'DJANGO_SECRET_KEY=%s\nDJANGO_DEBUG=false\nDJANGO_ALLOWED_HOSTS=api-test.phloz.app,127.0.0.1,localhost\nCORS_ALLOWED_ORIGINS=https://track.phloz.app\nUSE_SQLITE=true\nMEDIA_ROOT=%s/shared/media\nDJANGO_SCRIPT_NAME=/fms\n' "$secret" "${APP_ROOT}" > "${APP_ROOT}/shared/fms.env"
fi

# Uploads (template artwork) must outlive the release they were uploaded into.
# Without MEDIA_ROOT the settings default is BASE_DIR/media - inside the
# release directory - so every deploy silently orphaned them. Installs created
# before this line get it added here rather than needing a hand-edit.
mkdir -p "${APP_ROOT}/shared/media"
grep -q '^MEDIA_ROOT=' "${APP_ROOT}/shared/fms.env" \
    || echo "MEDIA_ROOT=${APP_ROOT}/shared/media" >> "${APP_ROOT}/shared/fms.env"

# nginx serves this app under /fms/ and strips the prefix before proxying, so
# Django has to be told the prefix exists. Without it every URL it generates -
# the admin's links, its form actions, its stylesheets - points at the domain
# root, which is a different application: the admin loads unstyled and its
# login form posts into the void.
grep -q '^DJANGO_SCRIPT_NAME=' "${APP_ROOT}/shared/fms.env" \
    || echo 'DJANGO_SCRIPT_NAME=/fms' >> "${APP_ROOT}/shared/fms.env"

set -a
. "${APP_ROOT}/shared/fms.env"
set +a
cd "$RELEASE"
"${APP_ROOT}/venv/bin/python" manage.py makemigrations fleet --noinput
"${APP_ROOT}/venv/bin/python" manage.py migrate --noinput
"${APP_ROOT}/venv/bin/python" manage.py collectstatic --noinput
ln -sfn "$RELEASE" "${APP_ROOT}/current"

sudo cp "$SCRIPT_DIR/phloz-fms.service" /etc/systemd/system/phloz-fms.service
sudo systemctl daemon-reload
sudo systemctl enable phloz-fms.service
sudo systemctl restart phloz-fms.service
sleep 2
sudo systemctl --no-pager --full status phloz-fms.service
curl -fsS http://127.0.0.1:8010/api/v1/health/
