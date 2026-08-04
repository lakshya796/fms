#!/usr/bin/env bash
#
# Phase 1 of 2: move the FMS from the shared SQLite file to PostgreSQL.
#
# Against a PostgreSQL server you already run (recommended):
#
#   sudo PG_HOST=ec2-13-235-89-56.ap-south-1.compute.amazonaws.com \
#        PG_ADMIN_USER=postgres PG_ADMIN_PASSWORD='...' \
#        bash scripts/migrate-to-postgres.sh
#
# Or installing PostgreSQL on this instance:
#
#   sudo bash scripts/migrate-to-postgres.sh
#
# The FMS gets its OWN database (default name `fms`). It must not share a database
# with another Django project: both would create auth_user, django_migrations,
# django_content_type and authtoken_token, and would corrupt each other. The script
# refuses to touch a database that already belongs to a different project.
#
# This phase changes the database only. When it finishes, the SAME release you run
# today is serving from PostgreSQL, so you can confirm the app is healthy before any
# code changes. Then run phase 2:  bash scripts/deploy-fms.sh
#
# If anything fails, the API is put back on its previous configuration and restarted,
# so you are never left with a stopped service.
set -euo pipefail

APP=/opt/phloz/fms
SHARED=$APP/shared
ENV_FILE=$SHARED/fms.env
SERVICE=phloz-fms
STAMP=$(date +%Y%m%d%H%M%S)
DUMP=$SHARED/sqlite-dump-$STAMP.json
ENV_BACKUP=$ENV_FILE.bak-$STAMP

PG_HOST=${PG_HOST:-127.0.0.1}
PG_PORT=${PG_PORT:-5432}
PG_DB=${PG_DB:-fms}
PG_USER=${PG_USER:-fms_app}
PG_ADMIN_USER=${PG_ADMIN_USER:-postgres}
PG_ADMIN_PASSWORD=${PG_ADMIN_PASSWORD:-}
RUN_AS=${RUN_AS:-ec2-user}

say() { printf '\n=== %s ===\n' "$1"; }
app_run() { sudo -u "$RUN_AS" bash -c "set -a; . '$ENV_FILE'; set +a; $*"; }
is_local() { [ "$PG_HOST" = "127.0.0.1" ] || [ "$PG_HOST" = "localhost" ]; }

# psql as the administrator, wherever the server lives.
admin_psql() {
  if is_local && [ -z "$PG_ADMIN_PASSWORD" ]; then
    sudo -u postgres psql -v ON_ERROR_STOP=1 "$@"
  else
    PGPASSWORD="$PG_ADMIN_PASSWORD" psql -v ON_ERROR_STOP=1 \
      -h "$PG_HOST" -p "$PG_PORT" -U "$PG_ADMIN_USER" "$@"
  fi
}

# Never leave the API down. If any step fails after the env file was rewritten,
# put the old configuration back and start the service again.
restore_on_failure() {
  local code=$?
  [ $code -eq 0 ] && return 0
  echo
  echo "!!! Migration failed (exit $code). Restoring the previous configuration."
  [ -f "$ENV_BACKUP" ] && cp -a "$ENV_BACKUP" "$ENV_FILE"
  systemctl start "$SERVICE" 2>/dev/null || true
  sleep 3
  if curl -fsS http://127.0.0.1:8010/api/v1/health/ >/dev/null 2>&1; then
    echo "API is back up on its previous database. Nothing was lost."
  else
    echo "API did not come back. Start it by hand:"
    echo "  sudo cp $ENV_BACKUP $ENV_FILE && sudo systemctl start $SERVICE"
  fi
  echo "The SQLite file is untouched at $SHARED/db.sqlite3"
  exit $code
}
trap restore_on_failure EXIT

[ -f "$ENV_FILE" ] || { echo "ABORT: $ENV_FILE not found"; exit 1; }
[ -f "$SHARED/db.sqlite3" ] || { echo "ABORT: $SHARED/db.sqlite3 not found - already on Postgres?"; exit 1; }
command -v psql >/dev/null || dnf install -y postgresql16 >/dev/null 2>&1 || dnf install -y postgresql15 >/dev/null

say "1. PostgreSQL server"
if is_local; then
  if sudo -u postgres psql -tAc "SELECT 1" >/dev/null 2>&1; then
    echo "PostgreSQL already running locally - reusing it"
  else
    dnf install -y postgresql16-server postgresql16 >/dev/null 2>&1 \
      || dnf install -y postgresql15-server postgresql15 >/dev/null
    /usr/bin/postgresql-setup --initdb >/dev/null 2>&1 || true
    systemctl enable --now postgresql
    echo "PostgreSQL installed and started"
  fi
  # Amazon Linux defaults host connections to `ident`, which fails for a role that has
  # no matching Unix user. Password authentication is what Django needs.
  HBA=$(sudo -u postgres psql -tAc "SHOW hba_file")
  if ! grep -qE '^\s*host\s+all\s+all\s+127\.0\.0\.1/32\s+scram-sha-256' "$HBA"; then
    cp -a "$HBA" "$HBA.bak-$STAMP"
    sed -i -E 's|^\s*(host\s+all\s+all\s+127\.0\.0\.1/32\s+)(ident|peer|trust)|\1scram-sha-256|' "$HBA"
    sed -i -E 's|^\s*(host\s+all\s+all\s+::1/128\s+)(ident|peer|trust)|\1scram-sha-256|' "$HBA"
    systemctl reload postgresql
    echo "pg_hba.conf set to password authentication for local TCP (backup: $HBA.bak-$STAMP)"
  fi
else
  echo "using the PostgreSQL server at $PG_HOST:$PG_PORT"
  [ -n "$PG_ADMIN_PASSWORD" ] || { echo "ABORT: set PG_ADMIN_PASSWORD for a remote server"; exit 1; }
fi
admin_psql -d postgres -tAc "SELECT version()" | head -1

say "2. A database of its own"
# Refuse to move in on another project's database: two Django projects sharing one
# database collide on django_migrations, auth_user, authtoken_token and content types.
if admin_psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$PG_DB'" | grep -q 1; then
  FOREIGN=$(admin_psql -d "$PG_DB" -tAc "
    SELECT count(*) FROM information_schema.tables
    WHERE table_schema='public' AND table_name='django_migrations'" 2>/dev/null || echo 0)
  if [ "${FOREIGN:-0}" != "0" ]; then
    OURS=$(admin_psql -d "$PG_DB" -tAc "
      SELECT count(*) FROM django_migrations WHERE app IN ('fleet','iam','accounting')" 2>/dev/null || echo 0)
    if [ "${OURS:-0}" = "0" ]; then
      echo "ABORT: database '$PG_DB' already holds another Django project's tables."
      echo "       Pick an unused name, e.g.  PG_DB=phloz_fms bash scripts/migrate-to-postgres.sh"
      exit 1
    fi
    echo "database '$PG_DB' already holds this project - reusing it"
  fi
else
  admin_psql -d postgres -c "CREATE DATABASE \"$PG_DB\"" >/dev/null
  echo "created database $PG_DB"
fi

PG_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
if [ "$PG_USER" = "$PG_ADMIN_USER" ]; then
  PG_PASSWORD=$PG_ADMIN_PASSWORD
  echo "using the administrator role $PG_USER for the application"
else
  admin_psql -d postgres >/dev/null <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$PG_USER') THEN
    CREATE ROLE "$PG_USER" LOGIN PASSWORD '$PG_PASSWORD';
  ELSE
    ALTER ROLE "$PG_USER" LOGIN PASSWORD '$PG_PASSWORD';
  END IF;
END \$\$;
SQL
  admin_psql -d postgres -c "ALTER DATABASE \"$PG_DB\" OWNER TO \"$PG_USER\"" >/dev/null
  admin_psql -d "$PG_DB" -c "GRANT ALL ON SCHEMA public TO \"$PG_USER\"" >/dev/null
  echo "role $PG_USER owns database $PG_DB"
fi

say "3. Stop the API and dump SQLite"
# Stop first, so nothing writes to SQLite after the dump is taken - those writes
# would be lost silently when the app switches over.
systemctl stop "$SERVICE"
cp -a "$SHARED/db.sqlite3" "$SHARED/db.sqlite3.bak-$STAMP"
echo "sqlite backup: $SHARED/db.sqlite3.bak-$STAMP"
cp -a "$ENV_FILE" "$ENV_BACKUP"
# contenttypes and permissions are recreated by migrate, so including them collides on
# load. Sessions are disposable. Everything else keeps its original primary keys.
app_run "USE_SQLITE=true '$APP/venv/bin/python' '$APP/current/manage.py' dumpdata \
  --exclude contenttypes --exclude auth.permission \
  --exclude admin.logentry --exclude sessions.session \
  --indent 2 --output '$DUMP'"
echo "dump: $DUMP ($(python3 -c "import json,sys; print(len(json.load(open('$DUMP'))))") records)"

say "4. Build the schema and load the data"
sed -i '/^USE_SQLITE=/d;/^POSTGRES_/d' "$ENV_FILE"
cat >> "$ENV_FILE" <<ENV
POSTGRES_HOST=$PG_HOST
POSTGRES_PORT=$PG_PORT
POSTGRES_DB=$PG_DB
POSTGRES_USER=$PG_USER
POSTGRES_PASSWORD=$PG_PASSWORD
ENV
chmod 600 "$ENV_FILE"; chown "$RUN_AS": "$ENV_FILE"
echo "env now points at $PG_DB@$PG_HOST (previous file kept at $ENV_BACKUP)"
app_run "'$APP/venv/bin/python' '$APP/current/manage.py' migrate --noinput" | tail -3
app_run "'$APP/venv/bin/python' '$APP/current/manage.py' loaddata '$DUMP'" | tail -2

say "5. Start the API on PostgreSQL and verify"
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

trap - EXIT
cat <<DONE

Done. The API is serving from PostgreSQL on the release you were already running.
Check the console and your data, then deploy the new code:

    bash scripts/deploy-fms.sh

To roll back to SQLite:
    sudo cp $ENV_BACKUP $ENV_FILE && sudo systemctl restart $SERVICE
DONE
