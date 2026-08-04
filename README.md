# Phloz Fleet ERP

Fleet management and transport ERP for Indian fleet owners: customer KYC, sales, LR booking,
manifests, trip sheets, own-fleet costing, driver settlements and invoicing — plus a FleetOps
layer modelled on the open-source [Fleetbase](https://github.com/fleetbase/fleetbase) platform.

## FleetOps modules

Consignment orders with waypoints and ePOD, public consignment tracking, service areas and
geofenced zones, places, vendors and attached fleets, GST-aware rate cards with a freight
estimator, diesel and mileage tracking, on-road expenses, driver-reported issues, statutory
compliance documents with renewal alerts, and preventive maintenance schedules.

See [docs/FLEETOPS.md](docs/FLEETOPS.md) for the API, the Fleetbase mapping and the India-specific
behaviour (GST/RCM, e-way bill, FASTag, RTO paperwork, driver bhatta).

## Repository layout

- `app/` — Next.js console (`/` workspace, `/track` public consignment tracking)
- `backend/` — Django REST API (`/api/v1/`), see [backend/README.md](backend/README.md)

## Deploying the API

```bash
cp .env.fms.example .env.fms   # then fill in secrets
docker compose --env-file .env.fms -f docker-compose.fms.yml up -d --build
```

Full deployment notes, including the Nginx proxy, are in [backend/README.md](backend/README.md).

## Running locally

```bash
# API
cd backend
pip install -r requirements.txt
export DJANGO_SECRET_KEY=dev DJANGO_ALLOWED_HOSTS=localhost CORS_ALLOWED_ORIGINS=http://localhost:3000 USE_SQLITE=true
python manage.py migrate
python manage.py seed_fleetops
python manage.py runserver

# Console
npm install
NEXT_PUBLIC_FMS_API_URL=http://127.0.0.1:8000 npm run dev
```
