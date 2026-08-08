# Phloz Fleet Management API

Django REST backend for the Phloz fleet-owner platform.

## Included resources

**Transport ERP** — customers/KYC, drivers, vehicles, lorry receipts, trips, tracking events,
sales quotations, maintenance work orders, invoices and driver settlements.

**FleetOps** (modelled on [Fleetbase](https://github.com/fleetbase/fleetbase)) — vendors, places,
service areas, geofenced zones, fleets, rate cards with a freight estimator and lane margin
projection, consignment orders with waypoints, tracking activity, the ePOD workflow, invoices
raised from the consignment and posted to the ledger, fuel entries with mileage, on-road trip
expenses, issues, statutory compliance documents and preventive maintenance schedules.

**Accounting** (`/api/v1/accounting/`) — chart of accounts, cost centres, double-entry journal
vouchers, vendor bills with TDS, receipts and payments, and reports for trial balance, P&L,
ledger, receivable and payable ageing, vehicle profitability and GST.

**Identity and access** (`/api/v1/iam/`) — organisation, branches, roles with a 19 permission
catalogue, users, and an audit trail.

Endpoint references: [../docs/FLEETOPS.md](../docs/FLEETOPS.md) and
[../docs/ACCOUNTING-AND-ADMIN.md](../docs/ACCOUNTING-AND-ADMIN.md).

## Local development

1. Create a virtual environment and install requirements.
2. Export `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS` and either
   `USE_SQLITE=true` or the `POSTGRES_*` variables.
3. Run `python manage.py migrate` (migrations are committed; do not generate them at runtime).
4. Run `python manage.py seed_accounting`, then `python manage.py seed_fleetops` for a
   realistic Indian demo dataset that is also billed and posted to the ledger. Run
   `python manage.py seed_voucher_portal` for the Voucher Portal's departments, voucher types,
   prefixes and default template (see [../docs/VOUCHER-PORTAL.md](../docs/VOUCHER-PORTAL.md)).
5. Run `python manage.py runserver` and open `/api/v1/health/` or `/api/v1/`.

## Tests

`python manage.py test` — 173 tests across fleet, accounting, iam, vouchers and voucher_portal.

## EC2 deployment

Copy `.env.fms.example` to `.env.fms`, set secrets, then run:

```bash
docker compose --env-file .env.fms -f docker-compose.fms.yml up -d --build
```

`--env-file .env.fms` is required: the database service resolves `${POSTGRES_*}` through Compose
variable interpolation, which does not read the service level `env_file`. Without it Postgres
starts with a blank password and refuses to initialise.

Migrations run automatically when the API container starts. Afterwards:

```bash
C="docker compose --env-file .env.fms -f docker-compose.fms.yml"
$C exec fms-api python manage.py createsuperuser
$C exec fms-api python manage.py seed_fleetops   # optional demo data
curl -s localhost:8010/api/v1/health/
```

The API binds only to EC2 localhost port 8010 so the existing Nginx instance can proxy
`api.track.phloz.app` safely.
