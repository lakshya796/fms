# MAIR Voucher Portal

A standalone voucher issuing and card-design application: a Django/DRF API and
a Next.js UI, packaged to run as two containers in one ECS Fargate task.

Everything the portal needs is inside this folder. It carries no dependency on
the fleet-management codebase it was extracted from — its own Django project,
its own Next app, its own migrations, its own tests.

## What it does

- **Design the card.** A free-form designer: drag text, boxes, rules, dynamic
  fields and the barcode anywhere on the card, at any size, in any colour, and
  watch the real server-rendered PDF update as you go. No fixed slots.
- **Every voucher gets a unique barcode.** Code128, from the voucher number,
  allocated under a row lock. The barcode element is mandatory and cannot be
  deleted or hidden — validation rejects a layout without one.
- **Generate in batches.** Pick a template, a prefix and a quantity; the API
  allocates numbers and renders one PDF per voucher plus a combined sheet.
- **Approval workflow.** Draft → submitted → approved → generated, with status
  history and notifications.
- **Reference data from the UI.** Departments, voucher types and prefixes are
  managed in the app, not in the database by hand.

## Layout

```
voucher-portal/
├── backend/                  Django 4.2 + DRF
│   ├── config/               settings, urls, wsgi/asgi, test runner
│   ├── voucher_portal/       the app: models, designer geometry, PDF, admin
│   ├── Dockerfile            multi-stage, collectstatic baked in, non-root
│   └── requirements.txt
├── frontend/                 Next.js 16 (App Router)
│   ├── app/voucher-portal/   the portal, including the designer
│   ├── app/lib/fms-api.ts    the only place the API base URL is read
│   ├── public/mair-logo.svg  brand mark — replace this file
│   └── Dockerfile            standalone output, non-root
├── deploy/ecs/               task definition + deployment runbook
├── docker-compose.yml        local run of the same topology
└── .env.example
```

## Run it locally

```bash
cp .env.example .env
docker compose up --build

# in another shell, once:
docker compose run --rm api python manage.py migrate
docker compose run --rm api python manage.py createsuperuser
docker compose run --rm api python manage.py seed_voucher_portal
```

Then open <http://localhost:3000/voucher-portal>. The Django admin is at
<http://localhost:8000/admin/>.

`seed_voucher_portal` creates the HR and Marketing departments, the
Employee/Marketing/Gift voucher types, the `EMP`/`MKT`/`MAIR` prefixes, and one
blank card to design on. It is idempotent.

### Without Docker

```bash
# API
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
DJANGO_SECRET_KEY=dev USE_SQLITE=true DJANGO_DEBUG=true python manage.py migrate
DJANGO_SECRET_KEY=dev USE_SQLITE=true DJANGO_DEBUG=true python manage.py runserver 8000

# UI
cd frontend
npm install
NEXT_PUBLIC_FMS_API_URL=http://localhost:8000 npm run dev
```

`USE_SQLITE=true` is for development only. SQLite cannot honour the row lock the
voucher-number allocator relies on, so two API processes on SQLite will hand out
duplicate voucher numbers.

## Tests

```bash
cd backend
DJANGO_SECRET_KEY=test USE_SQLITE=true python manage.py test    # 138 tests
cd ../frontend && npm run typecheck
```

The suite covers numbering under concurrency, the layout validator, PDF
rendering of every element type, the workflow transitions, permissions, and the
admin.

## Configuration

Required. The settings module reads them at import and fails loudly if a
required one is missing, rather than starting with a silent default.

| Variable | Notes |
| --- | --- |
| `DJANGO_SECRET_KEY` | Secrets Manager in AWS, never the task definition. |
| `POSTGRES_DB` / `_USER` / `_PASSWORD` / `_HOST` | Not needed when `USE_SQLITE=true`. |

Optional.

| Variable | Default | Notes |
| --- | --- | --- |
| `DJANGO_DEBUG` | `false` | |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | On ECS keep `*` or add the VPC CIDR — the ALB health check arrives by IP, and a 400 there gets the task killed in a loop. |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000` | The browser origin serving the UI. |
| `CORS_ALLOWED_ORIGIN_REGEXES` | — | For per-branch subdomains. Anchor at both ends or a lookalike domain matches too. |
| `MEDIA_ROOT` | `backend/media` | Uploaded card artwork. On ECS this must be EFS or S3. |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_CONN_MAX_AGE` | `60` | |
| `DJANGO_TIME_ZONE` | `Asia/Dubai` | |
| `DJANGO_LOG_LEVEL` | `INFO` | |
| `DJANGO_SCRIPT_NAME` | — | Only when a proxy mounts the API under a path prefix *and strips it*. Set wrongly, every admin link 404s. |
| `DJANGO_STATIC_MANIFEST` | `false` | Hashed static filenames. The Dockerfile turns it on after `collectstatic`; turning it on before means every page 500s. |
| `USE_SQLITE` | `false` | Development and tests only. |
| `NEXT_PUBLIC_FMS_API_URL` | `http://localhost:8000` | **Build-time.** Next inlines it into the bundle — rebuild the image to change it. |

## MAIR branding

Three places, and nothing else hard-codes the brand:

1. **`frontend/public/mair-logo.svg`** — currently a placeholder mark drawn to
   sit correctly in the header and sign-in card. Drop the official logo in at
   the same path (SVG or PNG) and every screen picks it up. Keep roughly a
   3.4:1 aspect ratio so the header height holds.
2. **`frontend/app/globals.css`**, the `:root` block — the palette hangs off
   `--mair-green: #0A4A3A`. Change that one value and buttons, focus rings,
   active tabs and links follow. `--voucher-purple` is kept as an alias of it so
   the component styles carried over from the original portal did not need
   rewriting line by line.
3. **`backend/voucher_portal/geometry.py`**, the `PALETTE` and default element
   colours — the ink colours used *on the cards themselves*, which are rendered
   server-side into the PDF and so cannot come from CSS.

## Deploying

See [`deploy/ecs/README.md`](deploy/ecs/README.md) for the task definition, the
build-and-push commands, the one-off migration task, and the ALB path rules.

Three things about that setup are worth knowing before the first deploy:

- **Migrations are not in the container start command.** With more than one
  replica, every container races to apply the same migration. Run the one-off
  task first.
- **Artwork needs the EFS mount.** Without it, uploaded card artwork is
  discarded on every deploy. The API degrades honestly — it reports the file as
  missing rather than 500ing — but the file is gone.
- **PDF generation runs in a background thread, not a queue.** A task killed
  mid-generation leaves the batch in `generating`. Give the ALB a deregistration
  delay of 60s or more, and prefer scaling up to recycling.
