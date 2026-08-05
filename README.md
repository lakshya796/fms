# Phloz Fleet ERP

Fleet management and transport ERP for Indian fleet owners: customer KYC, sales, LR booking,
manifests, trip sheets, own-fleet costing, driver settlements and invoicing — plus a FleetOps
layer modelled on the open-source [Fleetbase](https://github.com/fleetbase/fleetbase) platform.

## FleetOps modules

Consignment orders with waypoints, a full ePOD workflow (delivery OTP, driver capture with
shortage and damage, office review), invoices raised automatically from the rate card, public
consignment tracking, service areas and geofenced zones, places, vendors and attached fleets,
GST-aware rate cards with a freight estimator and a lane margin projection, diesel and mileage
tracking, on-road expenses, driver-reported issues, statutory compliance documents with renewal
alerts, and preventive maintenance schedules.

See [docs/FLEETOPS.md](docs/FLEETOPS.md) for the API, the Fleetbase mapping and the India-specific
behaviour (GST/RCM, e-way bill, FASTag, RTO paperwork, driver bhatta).

## Accounting, operations flow and user management

Double-entry accounting with an Indian transport chart of accounts, vendor bills with TDS,
receipts and payments, and seven financial reports including vehicle-wise profitability and
GST summary. Demand is captured as an indent, allocated to a truck and converted into a
priced consignment order. Logins carry a role and a branch, with a full audit trail.

Built for a 1000+ vehicle operation — see
[docs/ACCOUNTING-AND-ADMIN.md](docs/ACCOUNTING-AND-ADMIN.md).

## Repository layout

- `app/` — Next.js console (`/` workspace, `/track` public consignment tracking)
- `backend/` — Django REST API (`/api/v1/`), see [backend/README.md](backend/README.md)

## Deploying the API

On the EC2 instance (release directories, systemd and gunicorn):

```bash
sudo PG_ADMIN_PASSWORD='...' bash scripts/deploy-fms.sh
```

It sets up PostgreSQL, cuts a release, migrates, seeds, restarts only `phloz-fms` and
verifies. It never touches the Falcon9 or Baileys services. See
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

There is also a Docker Compose path for a clean host:

```bash
cp .env.fms.example .env.fms   # then fill in secrets
docker compose --env-file .env.fms -f docker-compose.fms.yml up -d --build
```

## Running locally

```bash
# API
cd backend
pip install -r requirements.txt
export DJANGO_SECRET_KEY=dev DJANGO_ALLOWED_HOSTS=localhost CORS_ALLOWED_ORIGINS=http://localhost:3000 USE_SQLITE=true
python manage.py migrate
python manage.py seed_accounting     # chart of accounts, roles, head office, fiscal year
python manage.py seed_fleetops       # demo fleet data, billed and posted to the ledger
python manage.py runserver

# Console
npm install
NEXT_PUBLIC_FMS_API_URL=http://127.0.0.1:8000 npm run dev
```
