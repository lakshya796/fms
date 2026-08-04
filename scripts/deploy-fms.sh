#!/usr/bin/env bash
#
# Deploy the Phloz FMS API onto the EC2 instance.
#
#   sudo PG_ADMIN_PASSWORD='...' bash scripts/deploy-fms.sh
#
# Everything is configurable from the environment; the defaults match the current
# deployment, so in practice only PG_ADMIN_PASSWORD is required the first time.
#
#   PG_HOST              PostgreSQL server            (default: the shared server below)
#   PG_ADMIN_USER        role that can create databases     (default: postgres)
#   PG_ADMIN_PASSWORD    its password                       (required on the first run)
#   PG_DB / PG_USER      the FMS database and its role      (default: fms / fms_app)
#   BRANCH               ref to deploy                      (default: the feature branch)
#   SEED=false           skip the demo dataset
#   ADMIN_PASSWORD       password for the fleetadmin login it creates
#
# WHAT THIS TOUCHES
#   /opt/phloz/fms/**            new release directory, the `current` symlink, fms.env
#   systemctl restart phloz-fms  the FMS gunicorn service, for about a second
#   PostgreSQL                   creates the `fms` database and the `fms_app` role only
#
# WHAT IT NEVER TOUCHES
#   phloz-api-test (:8000, Falcon9) and waweb-baileys - not restarted, not reconfigured
#   /opt/phloz/falcon9/**, their virtualenv, their database
#   Nginx, and any database other than the one named in PG_DB
#
# It refuses to use a database that already holds another Django project, and refuses
# to change the password of a PostgreSQL superuser role. Safe to re-run: the database,
# the seed data and the admin user are all idempotent.
set -euo pipefail

APP=/opt/phloz/fms
SHARED=$APP/shared
ENV_FILE=$SHARED/fms.env
SERVICE=phloz-fms
STAMP=$(date +%Y%m%d%H%M%S)
REL=$APP/releases/$STAMP
PREV=$(readlink -f "$APP/current" 2>/dev/null || echo "")

PG_HOST=${PG_HOST:-ec2-13-235-89-56.ap-south-1.compute.amazonaws.com}
PG_PORT=${PG_PORT:-5432}
PG_DB=${PG_DB:-fms}
PG_USER=${PG_USER:-fms_app}
PG_ADMIN_USER=${PG_ADMIN_USER:-postgres}
PG_ADMIN_PASSWORD=${PG_ADMIN_PASSWORD:-}
BRANCH=${BRANCH:-claude/fleet-management-india-r7xkey}
REPO=${REPO:-lakshya796/fms}
SEED=${SEED:-true}
RUN_AS=${RUN_AS:-ec2-user}
ALLOWED_HOSTS=${ALLOWED_HOSTS:-api-test.phloz.app,localhost,127.0.0.1}
CORS_ORIGINS=${CORS_ORIGINS:-https://track.phloz.app}
ADMIN_USER=${ADMIN_USER:-fleetadmin}
ADMIN_PASSWORD=${ADMIN_PASSWORD:-}

say() { printf '\n=== %s ===\n' "$1"; }
admin_psql() { PGPASSWORD="$PG_ADMIN_PASSWORD" psql -v ON_ERROR_STOP=1 -h "$PG_HOST" -p "$PG_PORT" -U "$PG_ADMIN_USER" "$@"; }
# Run as the app user with the environment file loaded. Quoting-safe.
app_run() { sudo -u "$RUN_AS" bash -c "set -a; . '$ENV_FILE'; set +a; cd '$REL'; $*"; }

[ -d "$APP" ] || { echo "ABORT: $APP not found - is this the right instance?"; exit 1; }
command -v psql >/dev/null || dnf install -y postgresql16 >/dev/null 2>&1 || dnf install -y postgresql15 >/dev/null

say "0. Preflight"
echo "branch:    $BRANCH"
echo "database:  $PG_DB@$PG_HOST"
echo "previous:  ${PREV:-none}"
if [ -z "$PG_ADMIN_PASSWORD" ] && ! grep -q '^POSTGRES_PASSWORD=' "$ENV_FILE" 2>/dev/null; then
  echo "ABORT: set PG_ADMIN_PASSWORD so the database can be created"; exit 1
fi

say "1. Database"
if [ -n "$PG_ADMIN_PASSWORD" ]; then
  admin_psql -d postgres -tAc "SELECT version()" | head -1
  if admin_psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$PG_DB'" | grep -q 1; then
    # Two Django projects cannot share one database: both create django_migrations,
    # auth_user, django_content_type and authtoken_token, and would corrupt each other.
    HAS_DJANGO=$(admin_psql -d "$PG_DB" -tAc "SELECT count(*) FROM information_schema.tables
      WHERE table_schema='public' AND table_name='django_migrations'" 2>/dev/null || echo 0)
    if [ "${HAS_DJANGO:-0}" != "0" ]; then
      OURS=$(admin_psql -d "$PG_DB" -tAc "SELECT count(*) FROM django_migrations
        WHERE app IN ('fleet','iam','accounting')" 2>/dev/null || echo 0)
      [ "${OURS:-0}" = "0" ] && {
        echo "ABORT: database '$PG_DB' holds another Django project. Choose an unused name:"
        echo "       PG_DB=phloz_fms sudo -E bash scripts/deploy-fms.sh"; exit 1; }
    fi
    echo "database $PG_DB already exists - reusing it"
  else
    admin_psql -d postgres -c "CREATE DATABASE \"$PG_DB\"" >/dev/null
    echo "created database $PG_DB"
  fi

  PG_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
  if [ "$PG_USER" = "$PG_ADMIN_USER" ]; then
    PG_PASSWORD=$PG_ADMIN_PASSWORD
  else
    IS_SUPER=$(admin_psql -d postgres -tAc "SELECT rolsuper FROM pg_roles WHERE rolname='$PG_USER'" 2>/dev/null || true)
    if [ "$IS_SUPER" = "t" ]; then
      echo "ABORT: '$PG_USER' is a PostgreSQL superuser. Changing its password could break"
      echo "       another application on this server. Use a dedicated role, e.g. PG_USER=fms_app"
      exit 1
    fi
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
  fi
  echo "role $PG_USER can reach $PG_DB"
else
  echo "reusing the credentials already in $ENV_FILE"
fi

say "2. Environment file"
mkdir -p "$SHARED"
# Keep the existing secret key if there is one: changing it invalidates sessions.
SECRET=$(grep -E '^DJANGO_SECRET_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)
[ -n "$SECRET" ] || SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
[ -f "$ENV_FILE" ] && cp -a "$ENV_FILE" "$ENV_FILE.bak-$STAMP"
if [ -n "$PG_ADMIN_PASSWORD" ]; then
  cat > "$ENV_FILE" <<ENV
DJANGO_SECRET_KEY=$SECRET
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=$ALLOWED_HOSTS
CORS_ALLOWED_ORIGINS=$CORS_ORIGINS
POSTGRES_HOST=$PG_HOST
POSTGRES_PORT=$PG_PORT
POSTGRES_DB=$PG_DB
POSTGRES_USER=$PG_USER
POSTGRES_PASSWORD=$PG_PASSWORD
POSTGRES_CONN_MAX_AGE=60
ENV
fi
chmod 600 "$ENV_FILE"; chown "$RUN_AS": "$ENV_FILE"
grep -E '^(DJANGO_ALLOWED_HOSTS|CORS_ALLOWED_ORIGINS|POSTGRES_HOST|POSTGRES_DB|POSTGRES_USER)=' "$ENV_FILE"

say "3. Cut the release"
rm -rf /tmp/fmsbuild && mkdir -p /tmp/fmsbuild
curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" \
  | tar -xz -C /tmp/fmsbuild --strip-components=1
mkdir -p "$REL"
# A release directory holds the contents of the repo's backend/ folder.
cp -a /tmp/fmsbuild/backend/. "$REL"/
chown -R "$RUN_AS": "$REL"
echo "new release: $REL"

say "4. Dependencies, schema and seed data"
sudo -u "$RUN_AS" "$APP/venv/bin/pip" install -q -r "$REL/requirements.txt"
app_run "'$APP/venv/bin/python' manage.py migrate --noinput" | tail -6
# The chart of accounts must exist before anything posts to the ledger.
app_run "'$APP/venv/bin/python' manage.py seed_accounting"
if [ "$SEED" = "true" ]; then
  app_run "'$APP/venv/bin/python' manage.py seed_fleetops" | tail -1
fi
app_run "'$APP/venv/bin/python' manage.py collectstatic --noinput" >/dev/null && echo "static files collected"

[ -n "$ADMIN_PASSWORD" ] || ADMIN_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")
CREATED=$(app_run "'$APP/venv/bin/python' manage.py shell -c \"
from django.contrib.auth.models import User
u, made = User.objects.get_or_create(username='$ADMIN_USER', defaults={'is_staff': True, 'is_superuser': True})
if made:
    u.set_password('$ADMIN_PASSWORD'); u.save(); print('created')
else:
    print('exists')
\"")
echo "admin login '$ADMIN_USER': $CREATED"

say "5. Flip and restart"
ln -sfn "$REL" "$APP/current"
systemctl restart "$SERVICE"
for attempt in $(seq 1 20); do
  curl -fsS http://127.0.0.1:8010/api/v1/health/ >/dev/null && { echo "API healthy after $attempt attempt(s)"; break; }
  [ "$attempt" = 20 ] && {
    echo "API did not come up. Recent logs:"; journalctl -u "$SERVICE" -n 40 --no-pager
    [ -n "$PREV" ] && echo "Roll back: ln -sfn $PREV $APP/current && sudo systemctl restart $SERVICE"
    exit 1; }
  sleep 3
done

say "6. Verify"
systemctl is-active "$SERVICE" phloz-api-test waweb-baileys
TOKEN=$(app_run "'$APP/venv/bin/python' manage.py shell -c \"
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
print(Token.objects.get_or_create(user=User.objects.get(username='$ADMIN_USER'))[0].key)
\"" | tail -1)
for path in orders indents trips fleets vendors places zones service-rates compliance-documents \
            accounting/accounts accounting/journal-entries accounting/reports/trial-balance \
            iam/users iam/roles iam/branches; do
  printf '  %-38s ' "$path"
  curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:8010/api/v1/$path/" -H "Authorization: Token $TOKEN"
done

cat <<DONE

=== Done ===
  release   $REL
  rollback  ${PREV:-none}${PREV:+  ->  ln -sfn $PREV $APP/current && sudo systemctl restart $SERVICE}

  console login   $ADMIN_USER / $ADMIN_PASSWORD
  API token       $TOKEN

The console is deployed separately - trigger an Amplify rebuild of $BRANCH, with
NEXT_PUBLIC_FMS_API_URL=https://api-test.phloz.app set in the Amplify environment.
DONE
