# Phloz Fleet Management API

Django REST backend for the Phloz fleet-owner platform.

## Included resources
Customers/KYC, drivers, vehicles, lorry receipts, trips, tracking events, invoices and settlements.

## Local development
1. Create a virtual environment and install requirements.
2. Run `python manage.py makemigrations fleet && python manage.py migrate`.
3. Run `python manage.py runserver`.
4. Open `/api/v1/health/` or `/api/v1/`.

## EC2 deployment
Copy `.env.fms.example` to `.env.fms`, set secrets, then run:
`docker compose -f docker-compose.fms.yml up -d --build`

The API binds only to EC2 localhost port 8010 so the existing Nginx instance can proxy `api.track.phloz.app` safely.
