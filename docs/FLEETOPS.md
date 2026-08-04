# FleetOps modules for Indian fleet owners

This document describes the fleet-management layer added to the Phloz FMS, modelled on the
open-source [Fleetbase](https://github.com/fleetbase/fleetbase) FleetOps extension and adapted
to how Indian transporters and fleet owners actually run their business (GST, e-way bills,
FASTag, RTO paperwork, driver bhatta and per-ton-km freight).

The original ERP modules (customer KYC, lorry receipts, trip sheets, invoices, driver
settlements) are unchanged. FleetOps sits alongside them and adds the operational spine.

## How this maps to Fleetbase

| Fleetbase FleetOps model | Here | Notes |
| --- | --- | --- |
| `Vendor` | `Vendor` | Attached transporters, brokers, workshops, tyre and fuel vendors with GSTIN, PAN, MSME number, TDS % and payment terms |
| `Place` | `Place` | Warehouses, plants, hubs, fuel stations, toll plazas, RTO check posts, with pincode and lat/lng |
| `ServiceArea` | `ServiceArea` | Named operating region (e.g. West India) with the states it covers |
| `Zone` | `Zone` | Circular geofence (centre + radius) inside a service area, with a point-in-zone lookup |
| `Fleet`, `FleetVehicle`, `FleetDriver` | `Fleet` | Vehicle and driver groups, optionally owned by a vendor, nestable via `parent` |
| `ServiceRate`, `ServiceRateFee` | `ServiceRate` | Per km, per ton-km, per kg, per hour and fixed-per-trip rate cards |
| `ServiceQuote` | `ServiceQuote` | A stored lane estimate with its full pricing breakdown |
| `Order`, `Payload` | `Order` | Consignment order from booking to ePOD, with its own tracking number |
| `Waypoint` | `Waypoint` | Ordered multi-drop stops |
| `TrackingNumber`, `TrackingStatus` | `Order.tracking_number`, `TrackingActivity` | Consignee-facing activity feed |
| `Proof` | `ProofOfDelivery` | Signature, photo or delivery OTP, with shortage and damage capture |
| `FuelReport` | `FuelEntry` | Diesel fill-ups with automatic mileage (km/l) against the previous odometer reading |
| `Issue` | `Issue` | Breakdowns, tyre failures, accidents, check-post delays |
| `MaintenanceSchedule` | `MaintenanceSchedule` | Preventive service by odometer and/or calendar interval |
| — (India specific) | `TripExpense` | Toll/FASTag, driver bhatta, loading, unloading, RTO fine, police, parking, halting |
| — (India specific) | `ComplianceDocument` | RC, insurance, fitness, national/state permit, PUC, road tax, FASTag KYC, GPS certificate, licence, Aadhaar, police verification |

Fleetbase stores zones as polygons on a spatial database. To keep this deployment on plain
PostgreSQL without PostGIS, zones here are circular geofences and containment is a haversine
distance check (`fleet.models.haversine_km`).

## Indian specifics baked in

- **GST on freight** — rate cards carry a GST percentage (5% GTA without ITC, 12% with ITC) and a
  `reverse_charge` flag. When RCM applies, the quote returns zero tax and marks the consignee as
  liable, which is the common GTA arrangement.
- **Fuel surcharge** — a percentage applied on freight before tax, the way diesel escalation
  clauses are written into Indian rate contracts.
- **Freight terms** — to-pay, paid, to-be-billed and COD, matching lorry receipt conventions.
- **E-way bill** number on every order.
- **On-road cash costs** — the expense categories are the ones a driver actually spends on:
  toll/FASTag, bhatta, loading, unloading, RTO fine, police, parking, permit and halting.
- **Statutory papers** — the compliance document types are the Indian set, with a renewal
  watchlist so a truck is never dispatched on an expired fitness certificate or PUC.
- **Money and dates** — `Asia/Kolkata`, `en-in`, and rupee formatting throughout the UI.

## API

All endpoints are under `/api/v1/` and require a token (`POST /api/v1/auth/token/`), except
health and public consignment tracking.

### Resources

`vendors/`, `service-areas/`, `zones/`, `places/`, `fleets/`, `service-rates/`, `service-quotes/`,
`orders/`, `waypoints/`, `tracking-activities/`, `proofs/`, `fuel-entries/`, `trip-expenses/`,
`issues/`, `compliance-documents/`, `maintenance-schedules/`

Every list endpoint supports field filters and free-text search, for example:

```
GET /api/v1/orders/?status=in_transit&customer=3
GET /api/v1/vendors/?vendor_type=transporter&search=roadlines
GET /api/v1/trip-expenses/?category=toll&status=pending
```

### Order lifecycle

```
POST /api/v1/orders/                      # book (number, tracking number, distance and price are derived)
POST /api/v1/orders/{id}/assign/          # {"driver": 1, "vehicle": 2}
POST /api/v1/orders/{id}/dispatch/        # marks vehicle and driver on-trip
POST /api/v1/orders/{id}/activity/        # {"status": "in_transit", "code": "GPS_PING", "city": "Panvel"}
POST /api/v1/orders/{id}/complete/        # captures ePOD: receiver, OTP, shortage, damage
POST /api/v1/orders/{id}/cancel/
POST /api/v1/orders/{id}/reprice/         # recompute freight and GST from the rate card
```

If the order has a rate card and both places have coordinates, the lane distance and the full
freight breakdown are computed on creation.

### Freight estimator

```
POST /api/v1/service-rates/quote/
{ "service_rate": 1, "distance_km": 150, "weight_kg": 12400, "halt_days": 0, "save_quote": true }
```

Returns freight, fuel surcharge, loading/unloading, taxable value, GST (or the RCM flag) and the
total. With `save_quote` it also persists a `ServiceQuote`.

### Operational lookups

```
GET  /api/v1/zones/locate/?lat=19.2967&lng=73.0631      # which geofences contain this point
GET  /api/v1/compliance-documents/expiring/?days=30     # renewal watchlist
GET  /api/v1/maintenance-schedules/due/                 # services due by km or date
GET  /api/v1/fuel-entries/mileage/                      # km/l and diesel spend per vehicle
GET  /api/v1/trip-expenses/summary/                     # spend split by category
GET  /api/v1/analytics/fleet/?days=30                   # utilisation, cost per km, on-time %
GET  /api/v1/track/{tracking_number}/                   # public, no auth, no pricing
POST /api/v1/fleets/{id}/assign/                        # {"vehicles": [1,2], "drivers": [3]}
POST /api/v1/issues/{id}/resolve/
POST /api/v1/trip-expenses/{id}/approve/
POST /api/v1/maintenance-schedules/{id}/complete/
```

## Console

New workspace sections in the Next.js console:

- **Orders** — a five-column board (booked, allocated, dispatched, in transit, delivered).
  Drag a card between columns to progress it, or click one to open the consignment with its
  allocation, waypoints and tracking feed. Columns highlight green where the move is allowed
  and red where it is not, and an illegal drop says why rather than failing quietly. Dropping
  onto "allocated" opens the allocation panel, because an order cannot be assigned without
  naming a driver and a vehicle.
- **Rates** — rate cards plus a freight estimator that shows the full GST breakdown.
- **Compliance** — expiry watchlist over 15/30/60/90 days, plus preventive services that are due.
- **Fleets, Vendors, Places, Zones, Fuel, Expenses, Issues** — live master-data tables with create
  forms.
- **Analytics** — utilisation, cost per km, average mileage, on-time delivery, diesel and expense
  split for the last 30 days.
- **`/track`** — a public page where a consignee can enter a tracking number; it shows status and
  movement history and never exposes pricing.

## Demo data

```
python manage.py seed_fleetops          # idempotent, safe to re-run
python manage.py seed_fleetops --reset  # wipe FleetOps records first
```

Seeds three service areas, seven zones, nine places, five vendors, two fleets, four rate cards,
four orders with waypoints and activity, fuel entries with realistic mileage, on-road expenses,
issues, compliance documents and maintenance schedules.

## Tests

```
python manage.py test fleet
```

Covers rate-card arithmetic (including minimum charge and reverse charge), geofence containment,
the full order lifecycle, ePOD, public tracking, mileage and odometer roll-up, expense summaries,
document expiry windows, maintenance intervals, fleet assignment and the analytics endpoint.
