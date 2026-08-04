# Deploying the FMS API

The instance runs release directories under `/opt/phloz/fms`, served by gunicorn under the
`phloz-fms` systemd unit on `127.0.0.1:8010`, proxied by Nginx at `/fms/`. Two other
services share the box — `phloz-api-test` on `:8000` and `waweb-baileys` — and neither
script below touches them.

```
/opt/phloz/fms
├── current -> releases/<timestamp>     # the live release
├── releases/<timestamp>/               # contents of the repo's backend/ folder
├── shared/fms.env                      # EnvironmentFile for the systemd unit
└── venv/                               # shared virtualenv
```

## One time: move to PostgreSQL

SQLite serialises writes, and double-entry accounting writes several rows per event. With
two gunicorn workers that shows up as `database is locked` under load, so Postgres is the
supported production database.

```bash
sudo bash scripts/migrate-to-postgres.sh
```

It installs PostgreSQL (or reuses one already on the box), creates the database and role,
writes the credentials into `shared/fms.env`, **stops the API** so nothing writes to SQLite
after the dump is taken, dumps with the currently running release, loads into Postgres, then
starts the API again — still on the same release. Nothing about the code changes, so you can
confirm the app is healthy before deploying anything new.

Backups it leaves behind: `shared/db.sqlite3.bak-<stamp>` and `shared/fms.env.bak-<stamp>`.

**Rollback:** `sudo cp shared/fms.env.bak-<stamp> shared/fms.env && sudo systemctl restart phloz-fms`
— the old file still has `USE_SQLITE=true`, and the SQLite file is untouched.

### Notes on the migration

- `contenttypes` and `auth.permission` are excluded from the dump because `migrate`
  recreates them; including them collides on load. Sessions are disposable.
- Everything else keeps its original primary keys, so foreign keys, many-to-many links,
  password hashes and API tokens all survive. Verified before shipping by migrating a copy
  of this exact schema and confirming the existing API token still authenticated.
- Sequences need no manual reset — Django's `loaddata` resets them after a successful load.

## Every deploy

```bash
bash scripts/deploy-fms.sh                  # deploys claude/fleet-management-india-r7xkey
BRANCH=main bash scripts/deploy-fms.sh      # or any other ref
API_TOKEN=xxxx bash scripts/deploy-fms.sh   # also checks the authenticated endpoints
```

What it does:

1. **Preflight** — reads `shared/fms.env`; takes a `pg_dump` if on Postgres, or backs up and
   sanity-checks the shared SQLite link if not
2. **Cuts a release** from the GitHub tarball into `releases/<timestamp>` (no git needed on
   the instance), linking the shared SQLite file in if you are still on SQLite — a fresh
   release directory would otherwise start against an empty database
3. **Installs dependencies, migrates, runs `seed_accounting`** (idempotent; the chart of
   accounts must exist before anything posts to the ledger) and collects static files
4. **Flips `current` and restarts only `phloz-fms`**, polling `/api/v1/health/` for up to a
   minute; on failure it prints the journal and the rollback command
5. **Verifies** the three services are active, and the endpoint list if `API_TOKEN` is set

**Rollback:** the script prints the previous release path.

```bash
ln -sfn /opt/phloz/fms/releases/<previous> /opt/phloz/fms/current
sudo systemctl restart phloz-fms
```

Migrations are additive — new tables, plus new columns carrying defaults — and the old code
selects explicit column lists, so it runs against the migrated schema. You do not need to
reverse a migration to roll back.

## The console is separate

`track.phloz.app` is a Next.js static export built by Amplify. Deploying the API does not
update it; trigger an Amplify rebuild of the same branch. The console needs
`NEXT_PUBLIC_FMS_API_URL=https://api-test.phloz.app` set in Amplify — the client appends
`/api/v1/` itself.

## Environment

`shared/fms.env` is read by the systemd unit via `EnvironmentFile`:

| Variable | Notes |
| --- | --- |
| `DJANGO_SECRET_KEY` | required |
| `DJANGO_ALLOWED_HOSTS` | must include `api-test.phloz.app` |
| `CORS_ALLOWED_ORIGINS` | must include `https://track.phloz.app`, no trailing slash |
| `POSTGRES_HOST/PORT/DB/USER/PASSWORD` | set by the migration script |
| `POSTGRES_CONN_MAX_AGE` | optional, defaults to 60 seconds of connection reuse |
| `USE_SQLITE` | local development only |

Nginx must pass `X-Forwarded-Proto`, or Django will treat requests as plain HTTP and the
secure session and CSRF cookies will not work.

## Worth revisiting at scale

`phloz-fms` runs 2 gunicorn workers. For a 1000+ vehicle operation with several branches
entering data at once, `(2 x cores) + 1` is the usual starting point — change `ExecStart` in
`/etc/systemd/system/phloz-fms.service`, then `sudo systemctl daemon-reload && sudo systemctl restart phloz-fms`.
