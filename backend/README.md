# Phloz Fleet Management API

Django REST backend for the Phloz fleet-owner platform.

## Included resources

**Transport ERP** — customers/KYC, drivers, vehicles, lorry receipts, trips, tracking events,
sales quotations, maintenance work orders, invoices and driver settlements.

**FleetOps** (modelled on [Fleetbase](https://github.com/fleetbase/fleetbase)) — vendors, places,
service areas, geofenced zones, fleets, rate cards and quotes, consignment orders with waypoints,
tracking activity and ePOD, fuel entries with mileage, on-road trip expenses, issues, statutory
compliance documents and preventive maintenance schedules.

Full endpoint reference: [../docs/FLEETOPS.md](../docs/FLEETOPS.md).

## Local development

1. Create a virtual environment and install requirements.
2. Export `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS` and either
   `USE_SQLITE=true` or the `POSTGRES_*` variables.
3. Run `python manage.py migrate` (migrations are committed; do not generate them at runtime).
4. Run `python manage.py seed_fleetops` for a realistic Indian demo dataset.
5. Run `python manage.py runserver` and open `/api/v1/health/` or `/api/v1/`.

## Tests

`python manage.py test fleet`

## EC2 deployment

Copy `.env.fms.example` to `.env.fms`, set secrets, then run:
`docker compose -f docker-compose.fms.yml up -d --build`

The API binds only to EC2 localhost port 8010 so the existing Nginx instance can proxy
`api.track.phloz.app` safely.
