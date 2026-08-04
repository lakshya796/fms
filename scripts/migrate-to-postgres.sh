#!/usr/bin/env bash
#
# Phase 1 of 2: move the FMS from the shared SQLite file to PostgreSQL.
#
#   sudo bash scripts/migrate-to-postgres.sh
#
# This script changes the database only - it does not deploy new code. When it
# finishes, the SAME release you are running today is serving from Postgres, so you
# can confirm the app is healthy before any code changes. Then run phase 2:
#
#   bash scripts/deploy-fms.sh
#
# Steps:
#   1. Install and start PostgreSQL, unless the instance already runs one
#   2. Create the database and role, and write the credentials into shared/fms.env
#   3. Stop phloz-fms so nothing writes to SQLite while it is being copied, then dump
#      using the running release, so the dump matches the schema the data lives in
#   4. Build the schema in Postgres and load the dump
#   5. Start phloz-fms again, now on Postgres, and verify
#
# Only phloz-fms is touched. phloz-api-test (:8000) and waweb-baileys keep running.
# The SQLite file is left in place, so rollback is one env change plus a restart.
#
# Sequences need no manual reset - Django's loaddata resets them after a successful
# load. That was confirmed against a copy of this schema before shipping this script.
set -euo pipefail

APP=/opt/phloz/fms
SHARED=$APP/shared
ENV_FILE=$SHARED/fms.env
SERVICE=phloz-fms
STAMP=$(date +%Y%m%d%H%M%S)
DUMP=$SHARED/sqlite-dump-$STAMP.json
PG_DB=${PG_DB:-phloz_fms}
PG_USER=${PG_USER:-phloz_fms}
RUN_AS=${RUN_AS:-ec2-user}

say() { printf '\n=== %s ===\n' "$1"; }
# Run a command as the app user with shared/fms.env loaded, quoting-safe.
app_run() { sudo -u "$RUN_AS" bash -c "set -a; . '$ENV_FILE'; set +a; $*"; }

[ -f "$ENV_FILE" ] || { echo "ABORT: $ENV_FILE not found"; exit 1; }
[ -f "$SHARED/db.sqlite3" ] || { echo "ABORT: $SHARED/db.sqlite3 not found - is this already on Postgres?"; exit 1; }

say "1. PostgreSQL server"
if sudo -u postgres psql -tAc "SELECT 1" >/dev/null 2>&1; then
  echo "PostgreSQL already running on this instance - reusing it"
else
  dnf install -y postgresql16-server postgresql16 >/dev/null 2>&1 \
    || dnf install -y postgresql15-server postgresql15 >/dev/null
  /usr/bin/postgresql-setup --initdb >/dev/null 2>&1 || true
  systemctl enable --now postgresql
  echo "PostgreSQL installed and started"
fi
sudo -u postgres psql -tAc "SELECT version()" | head -1

say "2. Database and role"
PG_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
sudo -u postgres psql -v ON_ERROR_STOP=1 >/dev/null <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$PG_USER') THEN
    CREATE ROLE $PG_USER LOGIN PASSWORD '$PG_PASSWORD';
  ELSE
    ALTER ROLE $PG_USER PASSWORD '$PG_PASSWORD';
  END IF;
END \$\$;
SQL
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$PG_DB'" | grep -q 1 \
  || sudo -u postgres createdb -O "$PG_USER" "$PG_DB"
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$PG_DB" \
  -c "GRANT ALL ON SCHEMA public TO $PG_USER;" >/dev/null
echo "database $PG_DB owned by $PG_USER is ready"

say "3. Stop the API and dump SQLite"
# Stopping first means nothing can write to SQLite after the dump is taken - otherwise
# those writes would be silently lost when the app switches over.
systemctl stop "$SERVICE"
cp -a "$SHARED/db.sqlite3" "$SHARED/db.sqlite3.bak-$STAMP"
echo "sqlite backup: $SHARED/db.sqlite3.bak-$STAMP"
# contenttypes and permissions are recreated by migrate, so including them collides on
# load. Sessions are disposable. Everything else keeps its original primary keys.
app_run "USE_SQLITE=true '$APP/venv/bin/python' '$APP/current/manage.py' dumpdata \
  --exclude contenttypes --exclude auth.permission \
  --exclude admin.logentry --exclude sessions.session \
  --indent 2 --output '$DUMP'"
echo "dump: $DUMP ($(grep -c '\"model\":' "$DUMP") records)"

say "4. Build the schema in Postgres and load the data"
cp -a "$ENV_FILE" "$ENV_FILE.bak-$STAMP"
sed -i '/^USE_SQLITE=/d;/^POSTGRES_/d' "$ENV_FILE"
cat >> "$ENV_FILE" <<ENV
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=$PG_DB
POSTGRES_USER=$PG_USER
POSTGRES_PASSWORD=$PG_PASSWORD
ENV
chmod 600 "$ENV_FILE"; chown "$RUN_AS": "$ENV_FILE"
echo "env now points at Postgres (previous file kept at $ENV_FILE.bak-$STAMP)"
app_run "'$APP/venv/bin/python' '$APP/current/manage.py' migrate --noinput" | tail -3
app_run "'$APP/venv/bin/python' '$APP/current/manage.py' loaddata '$DUMP'" | tail -2

say "5. Start the API on Postgres and verify"
systemctl start "$SERVICE"
sleep 4
systemctl is-active "$SERVICE" phloz-api-test waweb-baileys
curl -fsS http://127.0.0.1:8010/api/v1/health/ && echo
app_run "'$APP/venv/bin/python' '$APP/current/manage.py' shell -c \"
from django.contrib.auth.models import User
from fleet.models import Customer, Vehicle, Driver, Trip, LorryReceipt, Invoice
for model in (User, Customer, Vehicle, Driver, Trip, LorryReceipt, Invoice):
    print(f'  {model.__name__:14} {model.objects.count()}')
\""

cat <<DONE

Done. The API is serving from PostgreSQL on the release you were already running.
Check the console loads and your data is all there, then deploy the new code:

    bash scripts/deploy-fms.sh

To roll back to SQLite:
    sudo cp $ENV_FILE.bak-$STAMP $ENV_FILE && sudo systemctl restart $SERVICE
DONE
