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

## Dispatch planning (planned)

An implementation plan for a CVRP-based dispatch planning module: day-ahead multi-order routing
across own dry vehicles, own reefers and hired third-party capacity, with temperature
compatibility, time windows, GPS start positions and live re-planning, an own-versus-hire
decision priced per load, and a one-action commit into orders, trips and vehicle hires. Design
only — not built. See [docs/DISPATCH-PLANNING.md](docs/DISPATCH-PLANNING.md).

## Gift Voucher Desk (ADCOOP retail)

A separate, publicly accessible page for generating and issuing ADCOOP retail gift vouchers —
unrelated to the fleet domain, sharing this deployment purely for convenience. No login: create
a numbered voucher series with a value and validity window, issue vouchers with an optional
phone number, and print each one as a PDF with its barcode. See
[docs/GIFT-VOUCHERS.md](docs/GIFT-VOUCHERS.md).

## Voucher Portal (ADCOOP retail, staff login required)

An authenticated extension of the gift voucher desk above, covering the fuller retail voucher
workflow: percentage or fixed discounts, department/type-scoped prefixes with server-allocated
numbering, role- and department-scoped access (Administrator, Requester, Approver, Report Viewer),
a full draft → submit → approve/reject → generate approval workflow with in-app notifications,
bulk generation with an individual PDF per voucher plus one combined print PDF, manual or CSV bulk
issuing, redeem/cancel actions, department-level reporting with CSV export, and a multi-template
artwork library. A separate app from the public desk — neither shares a table with the other. See
[docs/VOUCHER-PORTAL.md](docs/VOUCHER-PORTAL.md).

## Repository layout

- `app/` — Next.js console (`/` workspace, `/track` public consignment tracking, `/vouchers`
  public gift voucher desk, `/voucher-portal` authenticated voucher portal)
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
