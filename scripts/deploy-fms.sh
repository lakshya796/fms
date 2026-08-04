#!/usr/bin/env bash
#
# Phase 2 of 2: deploy the FMS API onto the EC2 instance.
#
#   bash scripts/deploy-fms.sh                 # deploys the default branch below
#   BRANCH=main bash scripts/deploy-fms.sh     # or any other ref
#
# Cuts a new release directory alongside the existing ones, so your rollback path
# stays intact, then flips `current` and restarts ONLY phloz-fms. phloz-api-test
# (:8000) and waweb-baileys are never touched.
#
# Run scripts/migrate-to-postgres.sh first if this instance is still on SQLite. If it
# is not, this script links the shared SQLite file into the new release, because a
# fresh release directory would otherwise start with an empty database.
set -euo pipefail

APP=/opt/phloz/fms
SHARED=$APP/shared
ENV_FILE=$SHARED/fms.env
SERVICE=phloz-fms
BRANCH=${BRANCH:-claude/fleet-management-india-r7xkey}
REPO=${REPO:-lakshya796/fms}
STAMP=$(date +%Y%m%d%H%M%S)
REL=$APP/releases/$STAMP
PREV=$(readlink -f "$APP/current")

say() { printf '\n=== %s ===\n' "$1"; }
app_env() { set -a; . "$ENV_FILE"; set +a; }

[ -f "$ENV_FILE" ] || { echo "ABORT: $ENV_FILE not found"; exit 1; }
app_env

say "0. Preflight"
echo "branch:   $BRANCH"
echo "previous: $PREV"
if [ "${USE_SQLITE:-false}" = "true" ]; then
  echo "database: SQLite (run scripts/migrate-to-postgres.sh to move to Postgres)"
  [ -e "$SHARED/db.sqlite3" ] || { echo "ABORT: shared SQLite file missing"; exit 1; }
  if [ -e "$PREV/db.sqlite3" ] && [ ! -L "$PREV/db.sqlite3" ]; then
    echo "ABORT: $PREV/db.sqlite3 is a real file, not a link into shared/. Stop and ask."; exit 1
  fi
  cp -a "$SHARED/db.sqlite3" "$SHARED/db.sqlite3.bak-$STAMP"
  echo "sqlite backup: $SHARED/db.sqlite3.bak-$STAMP"
else
  echo "database: PostgreSQL ${POSTGRES_DB:-?}@${POSTGRES_HOST:-?}"
  PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" -tAc "SELECT 1" >/dev/null || { echo "ABORT: cannot reach Postgres"; exit 1; }
  # A dump before a schema change is cheap and has saved many afternoons.
  PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h "$POSTGRES_HOST" -U "$POSTGRES_USER" \
    "$POSTGRES_DB" | gzip > "$SHARED/pg-backup-$STAMP.sql.gz"
  echo "postgres backup: $SHARED/pg-backup-$STAMP.sql.gz"
fi

say "1. Fetch $BRANCH and cut the release"
rm -rf /tmp/fmsbuild && mkdir -p /tmp/fmsbuild
curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" \
  | tar -xz -C /tmp/fmsbuild --strip-components=1
mkdir -p "$REL"
# A release directory holds the contents of the repo's backend/ folder.
cp -a /tmp/fmsbuild/backend/. "$REL"/
echo "new release: $REL"
echo "commit:      $(cat /tmp/fmsbuild/.git_archival.txt 2>/dev/null || echo "$BRANCH")"

if [ "${USE_SQLITE:-false}" = "true" ]; then
  # Without this the new release starts against an empty database file.
  ln -sfn "$SHARED/db.sqlite3" "$REL/db.sqlite3"
  echo "linked shared SQLite: $(ls -l "$REL/db.sqlite3" | sed 's/.*-> //')"
fi

say "2. Dependencies, migrations, seed data, static files"
"$APP/venv/bin/pip" install -q -r "$REL/requirements.txt"
cd "$REL"
echo "--- migrations to apply ---"
"$APP/venv/bin/python" manage.py showmigrations --plan 2>/dev/null | grep '\[ \]' | tail -12 || echo "(none pending)"
"$APP/venv/bin/python" manage.py migrate --noinput | tail -6
# The chart of accounts must exist before anything posts to the ledger. Idempotent.
"$APP/venv/bin/python" manage.py seed_accounting
"$APP/venv/bin/python" manage.py collectstatic --noinput >/dev/null && echo "static files collected"

say "3. Flip and restart"
ln -sfn "$REL" "$APP/current"
sudo systemctl restart "$SERVICE"
for attempt in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8010/api/v1/health/ >/dev/null; then
    echo "API healthy after $attempt attempt(s)"
    break
  fi
  [ "$attempt" = 20 ] && {
    echo "API did not come up. Recent logs:"; sudo journalctl -u "$SERVICE" -n 40 --no-pager
    echo; echo "Roll back with: ln -sfn $PREV $APP/current && sudo systemctl restart $SERVICE"; exit 1; }
  sleep 3
done

say "4. Verify"
systemctl is-active "$SERVICE" phloz-api-test waweb-baileys
TOKEN=${API_TOKEN:-}
if [ -n "$TOKEN" ]; then
  for path in fleets vendors places zones orders service-rates compliance-documents indents trips \
              accounting/accounts accounting/journal-entries iam/users iam/roles; do
    printf '  %-32s ' "$path"
    curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:8010/api/v1/$path/" \
      -H "Authorization: Token $TOKEN"
  done
else
  echo "  (set API_TOKEN=... to check the authenticated endpoints too)"
fi

cat <<DONE

Deployed $BRANCH.
  previous release (rollback target): $PREV
  rollback: ln -sfn $PREV $APP/current && sudo systemctl restart $SERVICE

The console is a separate deployment - trigger an Amplify rebuild to pick up the UI.
DONE
