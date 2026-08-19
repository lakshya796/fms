# Deploying the FMS API

One script does everything:

```bash
sudo PG_ADMIN_PASSWORD='...' bash scripts/deploy-fms.sh
```

Only `PG_ADMIN_PASSWORD` is needed the first time; the defaults match the current
deployment. Re-running it is safe — the database, the seed data and the admin user are all
idempotent.

## What it touches, and what it does not

| Touched | Not touched |
| --- | --- |
| `/opt/phloz/fms/**` — new release, `current` symlink, `fms.env` | `phloz-api-test` (`:8000`, Falcon9) — not restarted, not reconfigured |
| `systemctl restart phloz-fms` — about a second of downtime | `waweb-baileys` |
| PostgreSQL: creates the `fms` database and the `fms_app` role | `/opt/phloz/falcon9/**`, its virtualenv, its database |
| | Nginx, and any database other than `PG_DB` |

Two guards enforce that boundary, both tested against a real server:

- It **refuses a database that already holds another Django project**. Two Django projects
  cannot share one: each creates `django_migrations`, `auth_user`, `django_content_type`
  and `authtoken_token`, and they would corrupt each other.
- It **refuses to change a PostgreSQL superuser's password**, so setting `PG_USER=postgres`
  by accident cannot break another application's login.

## Options

| Variable | Default | Notes |
| --- | --- | --- |
| `PG_HOST` | the shared PostgreSQL server | |
| `PG_ADMIN_USER` / `PG_ADMIN_PASSWORD` | `postgres` / — | needed to create the database |
| `PG_DB` / `PG_USER` | `fms` / `fms_app` | the FMS database and its own role |
| `BRANCH` | the feature branch | any ref |
| `SEED` | `true` | `SEED=false` to skip the demo dataset |
| `ADMIN_PASSWORD` | generated | for the `fleetadmin` login |
| `ALLOWED_HOSTS` | `api-test.phloz.app,localhost,127.0.0.1` | |
| `CORS_ORIGINS` | `https://track.phloz.app,https://main.d12iaal63qqmzf.amplifyapp.com` | comma-separated, no trailing slash on any origin |

## Fleet document storage

ePOD photos and PDFs are streamed to private S3 storage; they are never written
to the application server. Set `FMS_S3_BUCKET` (default `phlozmedia`) and
`FMS_S3_REGION` (default `ap-south-1`) in `shared/fms.env`. The EC2 instance role
must allow `s3:PutObject` and `s3:GetObject` for `fleet/pod-documents/*` in that
bucket. Downloads remain authenticated by streaming the private S3 object
through the order API.

## What it does

1. **Database** — creates `fms` and the `fms_app` role on the PostgreSQL server, after the
   two guards above
2. **Environment** — writes `shared/fms.env`, keeping the existing `DJANGO_SECRET_KEY` if
   there is one so sessions and tokens survive; backs up the previous file
3. **Release** — downloads the branch tarball into `releases/<timestamp>` (no git needed on
   the instance)
4. **Schema and data** — installs requirements, migrates, runs `seed_accounting` (the chart
   of accounts must exist before anything posts to the ledger), optionally seeds the demo
   fleet, collects static files, and ensures the `fleetadmin` superuser exists
5. **Flip and restart** — points `current` at the new release, restarts only `phloz-fms`,
   and polls `/api/v1/health/` for up to a minute; on failure it prints the journal and the
   rollback command
6. **Verify** — confirms the three services are active and checks 15 endpoints with a real
   token, printing the login and token at the end

## Rollback

The script prints the previous release path:

```bash
ln -sfn /opt/phloz/fms/releases/<previous> /opt/phloz/fms/current
sudo systemctl restart phloz-fms
```

Migrations are additive — new tables, and new columns carrying defaults — so the previous
release runs against the migrated schema without reversing anything.

## The console is separate

`track.phloz.app` is a Next.js static export built by Amplify. Deploying the API does not
update it; trigger an Amplify rebuild of the same branch, with
`NEXT_PUBLIC_FMS_API_URL=https://api-test.phloz.app` set in the Amplify environment — the
client appends `/api/v1/` itself.

Nginx must pass `X-Forwarded-Proto`, or Django treats requests as plain HTTP and the secure
session and CSRF cookies stop working.

## Voucher Portal media

The deploy script writes `MEDIA_ROOT=$SHARED/media` into `fms.env` so voucher artwork and
generated PDFs survive a release cut (see [docs/VOUCHER-PORTAL.md](VOUCHER-PORTAL.md)). Django
only serves that directory itself when `DEBUG=true`, which production isn't - add an nginx
location block so it's reachable over HTTPS:

```nginx
location /media/ {
    alias /opt/phloz/fms/shared/media/;
}
```

This becomes unnecessary once `VOUCHER_PORTAL_S3_BUCKET` (and AWS credentials) are set - PDFs
then go straight to S3 and this path is never used.

## Worth revisiting at scale

`phloz-fms` runs 2 gunicorn workers. For a 1000+ vehicle operation with several branches
entering data at once, `(2 x cores) + 1` is the usual starting point — change `ExecStart` in
`/etc/systemd/system/phloz-fms.service`, then
`sudo systemctl daemon-reload && sudo systemctl restart phloz-fms`.
