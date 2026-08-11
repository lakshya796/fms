# O2A-to-Settlement TMS — feature validation and implementation plan

An audit of this repository against the 27-point Order-to-Account (O2A) transport
management specification, followed by a plan for what is not there.

Verdicts are evidence-based: every "present" claim below points at the model, endpoint or
service that implements it. Everything else is a gap with a proposed design.

## Implementation update

Phases 0, 2 and 4 of the plan below have been built on branch
`claude/fms-feature-validation-logi4i` (PR #20) — the three phases identified as the
shortest path to the spec's central claim: one trip carrying one cost stack, margin on
a hired truck computable, and allocation as a ranked, costed recommendation rather than
a dispatcher's guess. All backend work; 278 tests pass, no regressions.

- **Phase 0** — `Order.ensure_trip()` (`fleet/models.py`) creates and reuses one trip per
  assigned order, wired into assign/dispatch/complete/cancel and `Indent.convert`,
  fixing the zero-fuel bug in `order_profitability`. `Vehicle.ownership` is now a closed
  own/attached/leased/outside choice with a `Vendor` FK. `VehicleStatusLog` and
  `set_vehicle_status()` back the spec's 12-state vocabulary, with a manual
  `POST /vehicles/{id}/set-status/` for breakdown/workshop/idle and a
  `GET /vehicles/{id}/status-history/`. `iam.OutboundMessage` +
  `iam/messaging.py` record and can resend every outbound email
  (`POST /iam/outbound-messages/{id}/resend/`); `EMAIL_BACKEND` defaults to console
  output locally, SMTP (including SES's SMTP interface) via env vars in production.
  Celery/Redis (item 0.4) was deliberately **not** added — nothing in Phases 0/2/4
  needed asynchronous processing, and it stays a prerequisite for Phase 3/6 instead.
- **Phase 2** — `fleet.VehicleHire` (`fleet/models.py`) carries the commercial terms of
  an outside-sourced trip: agreed rate and basis, loading/unloading, detention, toll
  responsibility, advance, payment terms. `fleet/vendor_billing.py` computes the payable
  and raises an idempotent `VendorBill` (now linked to the hire) posted to the ledger.
  `GET /orders/{id}/settlement/` is the four-sided sheet (customer/vendor/driver/vehicle).
  `POST /hires/{id}/send-confirmation/` sends the vendor confirmation email through the
  outbox.
- **Phase 4** — `GET /vehicles/availability/` ranks vehicles by distance from their last
  known position with a document-expiry flag. `fleet/allocation.py` scores own vehicles
  (dead km + laden km against this fleet's own running cost) and vendor capacity (the
  vendor's own hire history, or a flagged estimate) by expected profit, exposed at
  `POST /orders/{id}/recommend-vehicles/`. `POST /orders/{id}/confirm-vehicle/` commits
  the choice in one call: links or registers the vehicle, opens the trip once a driver
  is linked, raises a `VehicleHire` for outside-sourced capacity, flags expired
  documents, and sends the vendor confirmation.

**What this does not change:** the Next.js console has no screens yet for
recommend/confirm-vehicle, the settlement sheet, hires, or vehicle status/availability —
only the API exists. Phases 1 (vehicle requirement capture, tyres, generic documents),
3 (GPS/telematics/cold chain), 5 (itemised cost model, invoice line items), 6 (alerts,
MIS, control tower) and 7 (learned estimators) are unbuilt, as originally scoped. The
allocation scorer in Phase 4 is deliberately simple where later phases would sharpen
it: dead km is only computable when a vehicle's live position is known (Phase 3), and
the vendor cost estimate falls back to a flat markup over own cost when a vendor has no
hire history yet — both are flagged (`dead_km: null`, `estimated_cost: true`) rather
than silently guessed.

**Scorecard — 3 complete, 15 partial, 9 absent** (pre-implementation baseline; rows
marked ⬆ below now have real backend support that the table's original wording predates).

| # | Area | Verdict |
|---|------|---------|
| 1 | Order / O2A management | 🟡 Partial |
| 2 | Vehicle master | 🟡 Partial ⬆ ownership/vendor now real |
| 3 | Vehicle availability | 🟡 ⬆ `/vehicles/availability/`, status log |
| 4 | GPS vehicle tracking | 🟡 Thin |
| 5 | Nearby vehicle recommendation | 🟡 ⬆ `/orders/{id}/recommend-vehicles/` |
| 6 | Vehicle allocation & confirmation | 🟡 ⬆ `/orders/{id}/confirm-vehicle/` + vendor email |
| 7 | Outside-sourced / vendor vehicles | 🟡 ⬆ `VehicleHire` commercials |
| 8 | Freight rate recommendation | 🟡 Partial |
| 9 | Profitability analysis | 🟡 ⬆ `/orders/{id}/settlement/`, fuel bug fixed |
| 10 | Trip management | 🟡 ⬆ unified with Order via `ensure_trip()` |
| 11 | Driver management & payment | 🟡 Partial |
| 12 | Vehicle expense management | ✅ Present |
| 13 | Fuel management | ✅ Present |
| 14 | Cold-chain management | ❌ Absent |
| 15 | Document management | 🟡 Partial |
| 16 | Movement & location reports | ❌ Absent |
| 17 | POD & delivery management | ✅ Present |
| 18 | Freight billing | 🟡 Partial |
| 19 | Vendor billing & payment | 🟡 ⬆ `VendorBill.hire`, payable computation |
| 20 | Accounts & complete settlement | 🟡 ⬆ four-sided settlement sheet |
| 21 | Dashboard & MIS | 🟡 Partial |
| 22 | Alerts & notifications | ❌ Absent |
| 23 | AI-based vehicle allocation | 🟡 ⬆ deterministic scorer built, ML unbuilt |
| 24 | AI freight recommendation | ❌ Absent |
| 25 | Predictive vehicle availability | ❌ Absent (position-based availability only) |
| 26 | Predictive maintenance | 🟡 Preventive, not predictive |
| 27 | Control tower / command centre | ❌ Absent |

The system today is a solid **booking → delivery → billing → ledger** spine. What it is not
yet is a **sourcing and margin** system: the three things that would make it one — a vehicle's
live position, the commercial terms of a hired truck, and a cost model per kilometre — are all
missing, and most of the other gaps hang off those three.

---

## Two structural findings that gate everything else

Before the item-by-item audit, two problems in the current design constrain most of the plan.

### Finding 1 — There are two parallel operational spines that never join

**Status: resolved in PR #20.** `Order.ensure_trip()` now creates and reuses one trip per
assigned order; `order_profitability` reports real fuel cost. Left below as the original
diagnosis, since it explains why the fix took the shape it did.

`Trip` (`backend/fleet/models.py:52`) is the older LR/manifest spine: vehicle, driver, many
lorry receipts, dispatch/close. `Order` (`backend/fleet/models.py:339`) is the FleetOps
consignment spine: customer, places, waypoints, tracking, ePOD, rate card, invoice.

`Order.trip` exists as a nullable FK and is writable through the serializer, but **no application
flow ever populates it**. `IndentViewSet.convert` (`backend/fleet/views.py:777`) creates an Order
with no Trip; `OrderViewSet.dispatch_order` flips vehicle and driver status directly without one;
`seed_fleetops` never sets it; the console's order form has no field for it. In practice it is
always null.

The visible consequence is in `order_profitability` (`backend/fleet/views.py:814`):

```python
fuel = FuelEntry.objects.filter(trip=order.trip).aggregate(...) if order.trip_id else 0
```

Because `order.trip_id` is always null for orders booked through the console, **fuel cost is
always zero in order profitability**. Diesel is the largest single cost in the business, and it
silently does not reach the P&L of any consignment.

Every downstream requirement in the spec — trip-wise profit, settlement, driver payout, vendor
payable — assumes one trip identity carrying one cost stack. Unifying these is prerequisite work,
not cleanup.

### Finding 2 — There is no asynchronous or outbound-communication capability at all

**Status: partially resolved in PR #20.** `iam.OutboundMessage` + `iam/messaging.py` now
record and send email (console backend locally, SMTP in production), which is what
Phase 2's vendor confirmation needed. Celery/Redis async processing is still absent and
remains a prerequisite for Phase 3 (GPS polling) and Phase 6 (scheduled alerts).

`backend/requirements.txt` is Django, DRF, CORS headers, gunicorn, psycopg, whitenoise,
reportlab, boto3. There is no Celery, no Redis, no scheduler, no `EMAIL_BACKEND` in
`backend/phloz_fms/settings.py`, and no `send_mail` call anywhere in the fleet app.

That single absence blocks §6 (vendor email), all of §22 (alerts), the GPS polling in §4, and
the scheduled evaluation every predictive feature needs. It is the cheapest high-leverage thing
on this list.

---

## Item-by-item validation

### 1. Order / O2A management — 🟡 Partial

**Present.** `Order` carries number, auto tracking number, customer, branch, order type
(FTL/PTL/parcel/rental/reverse), pickup and dropoff as `Place` FKs, `scheduled_at`,
`dispatched_at`, `completed_at`, payload description, weight, volume, packages, declared value,
distance, freight/other/tax/total, COD, payment mode, e-way bill, priority, `pod_required`,
`special_instructions`. Multi-drop is modelled properly by `Waypoint` with sequence, planned and
actual arrival, per-stop contact. `Indent` (`models.py:766`) captures demand before a truck is
committed, with `allocate` and `convert` actions.

The traceability chain the spec asks for exists in fragments and is genuinely wired at the back
end: Indent → Order → TrackingActivity → ProofOfDelivery → Invoice → JournalEntry → Payment,
with billing hard-blocked until the ePOD is verified (`billing.py:54`).

**Missing.**

- **The entire "Vehicle Requirement" section.** No dry/reefer/cold-chain class, required vehicle
  type, required capacity, body type, material type, type of loading, temperature set point,
  direct-vs-milk-run flag, or special vehicle requirements. `Indent` has `vehicle_type` as free
  text and nothing else. Without this, allocation cannot be matched or recommended — this is the
  input side of §5, §6 and §23.
- **One datetime where the spec needs two.** `scheduled_at` is a single field; there is no
  separate *required vehicle reporting* time and *required delivery* time. Every "delayed trip"
  and on-time metric needs both.
- Loading and unloading requirements are only free-text `special_instructions`.
- POD requirement is a boolean, not a type (physical / e-POD / photo / temperature report).
- Number of delivery locations is derivable from waypoint count but is never captured as a
  requirement at booking, so it cannot be priced or matched.
- The chain breaks at the Trip link — see Finding 1.

### 2. Vehicle master — 🟡 Partial

**Present.** `Vehicle` (`models.py:37`): registration number, vehicle type, capacity, ownership,
status, GPS device id, insurance and permit expiry, make/model, chassis, engine, fuel type,
FASTag id, current odometer. Statutory papers are handled well by `ComplianceDocument`
(`models.py:682`) with 13 document types covering RC, insurance, fitness, national and state
permits, PUC, road tax, FASTag KYC, GPS/VLT certificate — each with issue date, expiry,
`reminder_days`, a computed `days_to_expiry`, a valid/expiring/expired `status` property, and a
`GET /compliance-documents/expiring/?days=` endpoint.

**Missing.**

- **Reefer / cold-chain classification** — zero occurrences of `temperature`, `reefer` or
  `cold_chain` in the entire backend.
- **Vendor/owner is not a link.** `ownership` is a free-text `CharField` defaulting to `"owned"`,
  with no FK to `Vendor` and no enum. The spec's core principle — distinguishing own / attached /
  outside-sourced — is not enforceable in the current schema.
- Body type, year of manufacture, IoT/temperature device, reefer details, contract details.
- **No tyre model of any kind.** "Tyre" exists only as a `Vendor` type and an `Issue` type.
- Maintenance history exists as records (`MaintenanceWorkOrder`, `MaintenanceSchedule`) but there
  is no per-vehicle history rollup.
- Expiry dates are present; **automatic** alerts are not — `/expiring/` is a pull endpoint that
  something must choose to call. Nothing does.

### 3. Vehicle availability — ❌ Absent

**Present.** `Vehicle.status` as free text, moved between `"available"` and `"on_trip"` by
dispatch and complete actions. The dashboard counts both.

**Missing.** Essentially the whole requirement. None of *idle, loaded, unloaded, under
maintenance, driver unavailable, breakdown, at customer location, awaiting loading, awaiting
unloading* exist as states. There is no current location on `Vehicle`, no current trip or
destination, no expected availability time or place, no driver-availability rollup, no dead-km
estimate, and no "available vehicles by location" view. `Driver` has `current_latitude` /
`current_longitude` fields, but nothing ever writes to them.

### 4. GPS vehicle tracking — 🟡 Thin

**Present.** `TrackingActivity` stores geo-tagged status events; `POST /orders/{id}/activity/`
accepts pushes from a driver app or telematics webhook. `Order.current_position()` finds the last
fix and `progress_percent` estimates lane progress by straight-line haversine. `TrackingEvent`
records speed against a trip. `Zone` provides circular geofences with `contains()` and a
`GET /zones/locate/?lat=&lng=` lookup. Public consignee tracking works at `/track/{number}`.

**Missing.** The integration itself — there is no GPS provider adapter, no polling job, no device
registry beyond a `gps_device_id` string, and no position time-series table. Consequently: no
planned-vs-actual route, no ETA, no GPS-derived actual kilometres, no running / idle / stoppage
time, no excessive-idling detection, no route-deviation detection (`"route"` exists only as a
manually-raised `Issue` type), no automatic geofence entry/exit with arrival and departure times,
and no trip history replay.

### 5. Nearby vehicle recommendation — ❌ Absent

Nothing exists. No candidate search, no distance-from-origin ranking, no dead-km computation, no
scoring of any kind. Requires §3 and §4 first.

### 6. Vehicle allocation & confirmation — 🟡 Manual only

**Present.** `Indent.allocate` commits a vehicle and driver by id; `Order.assign`
(`views.py:293`) does the same and moves the order to `assigned`; `dispatch_order` flips vehicle
and driver to `on_trip` and logs the activity. The console has a drag-and-drop allocation board.
`Fleet` groups vehicles and drivers, optionally under a vendor.

**Missing.** No recommendation of any kind — allocation is "the dispatcher picks an id". No
document-validity check at the point of allocation. No vendor linkage written on confirmation.

**And the vendor email does not exist in any form** — no email backend configured, no send call,
no template, no record of what was sent, no resend. This is a named, itemised requirement (order
number, customer, origin, destination, reporting and delivery times, material, quantity, vehicle,
capacity, driver, temperature, route mode, stops, instructions, agreed rate) and none of its
plumbing is present.

### 7. Outside-sourced / vendor vehicle management — 🟡 Partial

**Present.** The `Vendor` master is genuinely good (`models.py:133`): name, code, type, contact
person, phone, email, GSTIN, PAN, MSME number, address, city, state, bank account, IFSC,
`payment_terms_days`, `tds_percent`, status. `Order.vendor` and `Fleet.vendor` FKs exist.
`VendorBill` in accounting handles TDS the way Indian transporters need.

**Missing — and this is the largest single gap in the audit.**

- No `Vehicle → Vendor` link and no own/attached/outside enum (see §2).
- No capture of an outside vehicle's own details: the hired truck's number, type, capacity,
  temperature class, and its driver's name, mobile and licence. The `Driver` master is shaped for
  own staff (unique phone, unique licence number, salary, joining date) and is the wrong home for
  a vendor's driver on a one-off hire.
- No vendor documents — `ComplianceDocument` links only to vehicle or driver.
- **The entire commercial side of a hire is absent**: agreed vendor rate, rate basis
  (trip/km/day/ton), contract vs spot, loading and unloading charges, detention terms, toll
  responsibility, other agreed charges, advance, balance payable, payment terms.

The spec's stated reason for this section — *"The system should maintain both Customer Freight
Rate and Outside Vehicle/Vendor Agreed Rate. This is essential for calculating the expected
margin"* — is exactly right, and today only the customer side exists. **Margin on a hired truck
is not computable in this system.** Since hired capacity is where most Indian fleet operators'
volume sits, this gap alone prevents §8, §9, §19, §20 and §23 from being meaningful.

### 8. Freight rate recommendation — 🟡 Partial

**Present.** `ServiceRate` (`models.py:258`) is a capable rate card: per-km, per-ton-km, per-kg,
per-trip and per-hour bases, base charge, minimum charge, loading, unloading, halting per day,
fuel surcharge %, GST % with reverse-charge handling, effective-from/to dates, and a `.quote()`
that returns a full breakdown. `POST /service-rates/project/` runs `project_lane`
(`billing.py:117`), which is better than most systems at this stage: it derives diesel price and
mileage from **this fleet's own recorded fill-ups** rather than an assumed figure, computes fuel
cost and on-road cost per km, and returns margin, margin %, revenue per km, cost per km,
break-even rate per km and a monthly rollup.

**Missing.**

- **Dead / empty kilometres are nowhere in the cost basis** — the single most important variable
  in the spec's sourcing logic.
- Cost heads are collapsed. The spec lists diesel, toll, driver allowance, parking, maintenance,
  tyres, depreciation, insurance, permit, loading/unloading and detention separately;
  `project_lane` has exactly two — `fuel_cost` and one blended `on_road_cost_per_km` averaged
  from historical `TripExpense`. Depreciation, insurance and permit are not in it at all, because
  they are fixed costs that never appear as trip expenses.
- No target profit margin input, and no **Minimum → Recommended → Expected Revenue → Estimated
  Cost → Expected Profit** ladder. `project_lane` prices one rate card and reports the margin
  that falls out; it does not solve for a freight figure.
- No contract-rate flag to skip cost analysis while still tracking the trip.

### 9. Profitability analysis — 🟡 Partial

**Present.** Three real pieces: `project_lane` (pre-trip, one lane, one rate card),
`GET /orders/{id}/profitability/` (post-trip actual: revenue, trip expenses, fuel, total cost,
profit, margin %, cost per km), and a ledger-derived `vehicle-profitability` report.

**Missing.** The comparison table the spec describes — several candidate vehicles side by side
with source (own/vendor), dead km, cost, revenue, expected profit and a recommendation — does not
exist, and cannot until §5 and §7 land. Vendor rate as a cost line does not exist.

Also note the fuel bug from Finding 1: actual order profitability currently reports fuel as zero.

### 10. Trip management — 🟡 Split / thin

**Present.** `Trip` has number, vehicle, driver, lorry receipts, origin, destination, planned and
actual departure, arrival, advance, estimated cost, status, with `dispatch` and `close` actions
and a Kanban board in the console.

**Missing from the trip sheet.** Order link (never populated — Finding 1), customer, own/vendor
flag, transport owner, route, direct/milk-run, delivery locations, planned and expected km,
reporting time, expected delivery time, customer freight, vendor agreed rate, contract rate,
temperature requirement, trip instructions. The `Order` side carries some of these; the `Trip`
side carries the vehicle-cost stack. Neither alone is the trip sheet the spec describes.

### 11. Driver management & payment — 🟡 Partial

**Present.** `Driver` has name, phone, licence number and expiry, status, Aadhaar, date of
joining, home city, `monthly_salary`, `daily_allowance`. `Settlement` links trip and driver with
advance, approved expenses, net payable and status. Driver documents go through
`ComplianceDocument`. `Payment` can be addressed to a driver and posted to the ledger.

**Missing.** Trip allowance as distinct from daily allowance, incentive, overtime, deductions,
other payments — the spec's formula
(`Earnings + Allowances + Incentives − Advances − Deductions`) has three terms with no home.
`Settlement` collapses everything into `approved_expenses` and `net_payable`, **both typed in by
hand** — there is no service that computes a settlement from trips run, days out, bhatta rate and
advances drawn. No vehicle-allocation history on the driver record.

### 12. Vehicle expense management — ✅ Present

**Present.** `TripExpense` (`models.py:635`) covers diesel, toll/FASTag, driver bhatta, loading,
unloading, RTO fine, police/checkpost, parking, on-road repair, permit/border tax, halting and
other — with FKs to trip, order, vehicle, driver *and* vendor, an approval action, and a
`/trip-expenses/summary/` breakdown by category. The `Vehicle → Trip → Order → Customer` linkage
the spec asks for is there (customer via the order).

**Gaps, minor.** No categories for tyres, scheduled maintenance (distinct from on-road repair),
FASTag recharge, insurance, challan (RTO fine is close), washing or breakdown. More
significantly, **approved expenses do not post to the ledger** — only invoices, vendor bills and
payments do — so ledger-derived profitability understates cost.

### 13. Fuel management — ✅ Present

**Present.** `FuelEntry` (`models.py:600`) captures date, vehicle, litres, rate, amount, station
(FK or free text), odometer, trip, driver, payment method and invoice number. `save()` computes
`mileage_kmpl` against the previous fill's odometer automatically and rolls the vehicle's
odometer forward. `GET /fuel-entries/mileage/` returns fills, litres, spend and average km/l per
vehicle; fleet analytics adds average mileage and cost per km.

**Missing.** The spec's diagnostic half: no GPS-km versus reported-km reconciliation (needs §4),
no abnormal-mileage flagging, no excess-consumption detection, no pilferage signal. `fuel_theft`
exists only as a manually-raised `Issue` type — a place to record a suspicion, not a way to
generate one.

### 14. Cold-chain management — ❌ Absent

Nothing. No temperature field, no set point, no reading, no excursion, no reefer on/off, no door
sensor, no loading or delivery temperature, no temperature report. Confirmed by grep: zero
matches for `temperature`, `reefer` or `cold_chain` across the backend and console.

### 15. Document management — 🟡 Partial

**Present.** `ComplianceDocument` handles vehicle and driver papers well — 13 statutory types,
issue and expiry dates, per-document `reminder_days`, a `file_url`, and an `/expiring/` query.

**Missing.** Vendor documents (no vendor FK on the model). Trip and customer documents — order
copy, invoice, delivery documents, temperature report — have no home; the ePOD has a single
`file_url`. There is **no file upload path for the fleet app at all**: `file_url` is a URL string
someone must produce elsewhere. (The voucher portal has a working S3/local storage helper at
`backend/voucher_portal/storage.py` — that pattern can be lifted.) And again, alerts are pull-only.

### 16. Movement & location reports — ❌ Absent

No running time, idle time, stoppage time, arrival or departure times, available time, total km
from GPS, current or last location, vehicles-by-location, or vehicles-becoming-available. All of
it depends on §4.

### 17. POD & delivery management — ✅ Present

The strongest area in the codebase, and materially ahead of the spec in places.

**Present.** `ProofOfDelivery` (`models.py:482`) implements a real ePOD workflow: a six-digit OTP
issued to the consignee with a 24-hour expiry, quoted back by the driver and verified server-side;
receiver name and phone; remarks; signature/photo URL; `shortage_kg`; `damage_reported`;
geo-tagged, timestamped capture; and an office review with verify/reject and a mandatory rejection
reason. `settle()` auto-clears a clean confirmed capture and holds anything with a shortage or
damage for review — the correct rule, since a shortage becomes a deduction on the bill. Billing is
hard-blocked until a proof reaches `verified` (`billing.py:54`).

Beyond the spec, it tracks the physical copy home by courier: courier name, AWB, status,
dispatched/expected/received dates, a lost state, an `courier_overdue` property and a
`/proofs/couriers-pending/` queue.

**Gaps, minor.** Rejection quantity is not distinct from the damage boolean. Only one photograph
can be attached (single URL). `/proofs/pending/` is the *review* queue — captures held for the
office — not the spec's "Delivered but POD pending" report, which is the inverse and does not
exist.

### 18. Freight billing — 🟡 Partial

**Present.** `build_invoice_from_order` (`billing.py:41`) is careful work: idempotent (a second
call returns the existing invoice rather than double-billing), re-prices from the rate card so a
mid-contract revision is picked up, refuses to bill a cancelled or incomplete consignment, refuses
to bill without a verified ePOD, sets GST percent, reverse-charge and place of supply from the
lane, and posts the invoice to the ledger in the same transaction. `Invoice.save()` recomputes the
total so it can never drift.

**Missing.** Everything beyond rate-card freight is one hand-typed `additional_charges` number.
There is **no invoice line-item model**, so detention, additional kilometres, extra stops, and
loading/unloading cannot be itemised, evidenced or disputed — and none of them are computed from
what actually happened on the trip.

### 19. Vendor billing & payment — 🟡 Partial

**Present.** On the accounting side this is well built: `VendorBill` with taxable amount, GST,
TDS, total, paid amount, `balance_due`, cost centre, expense account and ledger posting; `Payment`
and `PaymentAllocation` for money in and out with UTR/cheque reference and mode; a payable-ageing
report.

**Missing.** The operational half. A `VendorBill` cannot be linked to the order, trip or vehicle
it pays for — the FK does not exist. Nothing computes the payable from the hire (agreed rate +
approved extras − advance − deductions), because the hire terms themselves do not exist (§7).
There is no advance tracking against a trip, and no
**Vendor Payable → Advance → Balance → Payment Status → Final Settlement** view.

### 20. Accounts & complete settlement — 🟡 Partial

**Present.** A proper double-entry core: `Account` with a transport chart of accounts,
`JournalEntry` / `JournalLine` with a balance check and reversal, `CostCentre` by vehicle, branch,
route or driver, `FiscalYear`. Seven reports — trial balance, P&L, account ledger, receivable
ageing, payable ageing, vehicle profitability, GST summary.

**Missing.** The single settlement sheet the spec describes — customer side, vendor side, driver
side and vehicle side of one trip on one screen — does not exist. Neither does an actual-versus-
estimate close. And because trip expenses and fuel never post to the ledger (§12), ledger-derived
profitability is structurally understated.

### 21. Dashboard & MIS — 🟡 Partial

**Present.** `GET /dashboard/` returns customers, KYC pending, vehicles, on-trip, available,
active trips, LRs, open invoices, invoice total, pending settlements, orders, active and completed
orders, order revenue, fleets, vendors, places, service areas, zones, open issues, documents
expiring, 30-day fuel spend and trip expenses. `GET /analytics/fleet/` adds utilisation %,
on-time %, average mileage, estimated km run, cost per km and an expense split by category.

**Missing.** Customer-wise, route-wise and vendor-wise profitability; own-versus-vendor
comparison; a trip-wise profitability *report* (only the single-order endpoint exists); delayed
trips; pending POD count; route deviations; excessive idling; available vehicles by location;
future availability. Receivables and payables are computed in the accounting reports but are not
on the dashboard.

### 22. Alerts & notifications — ❌ Absent

**Present.** Two pull endpoints — `/compliance-documents/expiring/` and
`/maintenance-schedules/due/` — plus `Issue` as a manual incident record.

**Missing.** Everything the word "automatic" implies. There is no alert or notification model in
the fleet app, no rule definitions, no delivery channel (no email, SMS, push or in-app feed), and
no scheduler to evaluate anything. Of the 21 alert types the spec names, **zero** are delivered;
two are queryable if someone thinks to ask.

(A `Notification` model does exist — but in `voucher_portal`, for the unrelated retail voucher
approval workflow. Its shape is a reasonable reference, not a reusable component.)

### 23. AI-based vehicle allocation — ❌ Absent
### 24. AI freight recommendation — ❌ Absent

Neither exists. Worth separating two things the spec bundles: the *decision framework* in §23 and
§24 is deterministic — score candidates on dead km, fuel, toll, fit and expected profit; price
against cost plus a target margin. That needs no machine learning and is the Phase 4/5 work below.
The genuinely learned part is the *estimators* underneath (what will this lane actually cost,
what rate will this customer actually accept, how long will this vehicle really take), and those
need trip history this system has not accumulated yet.

`project_lane` is a deterministic cost model over recent fuel and expense averages. It is a
sensible foundation for §24 and not an implementation of it.

### 25. Predictive vehicle availability — ❌ Absent

No ETA, no projected free-at time or place, no matching of future availability against pending
orders. Needs §4.

### 26. Predictive maintenance — 🟡 Preventive, not predictive

**Present.** `MaintenanceSchedule` (`models.py:717`) drives preventive service off odometer or
calendar intervals, auto-computing `next_due_km` and `next_due_date`, with `km_remaining`,
`is_due` and a `/due/` endpoint, plus a `complete` action that rolls the schedule forward.
`MaintenanceWorkOrder` records the job.

**Missing.** The predictive half: no engine hours, no tyre usage tracking, no breakdown-history
modelling, no failure-risk scoring. Interval-based servicing is the right foundation, but it
answers "what is due" rather than "what is about to fail".

### 27. Control tower / command centre — ❌ Absent

No single operations screen, and no drill-down from company → location → vehicle → trip → order →
expense → billing → payment → settlement. The console has ten-plus separate module pages; nothing
composes them.

---

## Implementation plan

Seven phases, ordered by dependency rather than by spec number. Each phase is independently
shippable. Sizing assumes one full-stack engineer familiar with the codebase; "week" means a
5-day working week.

The three fastest routes to visible value are marked **⚡**.

### Phase 0 — Structural prerequisites · ~2 weeks · ⚡ · ✅ Built (PR #20)

Nothing in later phases is safe until these land.

**0.1 Unify the Trip and Order spines.** Make `Order` the commercial record and `Trip` the
execution record, with a non-null `Trip.order` FK. Create the trip inside
`OrderViewSet.assign` / `dispatch_order` and inside `IndentViewSet.convert`, so every consignment
has exactly one trip carrying its cost stack. Backfill existing orders with a data migration.
Then fix `order_profitability` to aggregate fuel via the trip — the zero-fuel bug disappears on
its own.

*Decision to confirm:* whether legacy LR-only trips (no order) remain valid. Recommend yes, with
`order` nullable but always set for new work, to avoid a risky backfill of historical data.

**0.2 Vehicle ownership as a first-class concept.** Replace `Vehicle.ownership` free text with
`OWNERSHIP_TYPES = [("own", …), ("attached", …), ("outside", …)]`, add `Vehicle.vendor` FK
(null for own), and add `owner_name` / `contract_reference`. This is the spec's stated core
principle and every margin report keys off it.

**0.3 Vehicle status vocabulary.** Enumerate the 12 states from §3 as `VEHICLE_STATUSES`, add a
`VehicleStatusLog` (vehicle, status, from/to timestamps, place, trip, reason) written on every
transition. The log is what §16's running/idle/available-time report reads from, so it is worth
having from day one rather than reconstructing later.

**0.4 Async and scheduling.** Add Celery with Redis, plus `celery-beat` for periodic work.
Alternative if the team wants to stay light: Django management commands driven by cron on the EC2
host, which fits the existing `scripts/deploy-fms.sh` deployment and needs no new services.
Recommend Celery — GPS polling and alert fan-out will both want retries and concurrency.

**0.5 Outbound communication.** Configure `EMAIL_BACKEND` (SES via the existing `boto3`
dependency is the least new infrastructure) and add an `OutboundMessage` model — channel, to,
subject, body, template key, related object, status, sent/failed timestamps, error, `retry_count`.
Every outbound message is recorded and resendable, which §6 explicitly requires.

*Deliverable:* one trip identity, real ownership types, a status history, a task queue and a
recorded, resendable outbox.

### Phase 1 — Requirement capture and master-data depth · ~2 weeks

**1.1 `VehicleRequirement`** — a one-to-one on `Order` (and mirrored on `Indent`, since the
requirement is stated at demand time). Fields: `temperature_control` (dry / reefer / cold-chain),
`vehicle_type`, `capacity_kg`, `body_type`, `material_type`, `loading_type`, `temp_set_point_c`,
`temp_min_c`, `temp_max_c`, `route_mode` (direct / milk-run), `delivery_points`,
`reporting_at`, `deliver_by`, `loading_requirements`, `unloading_requirements`,
`special_requirements`, `pod_type`. Add `reporting_at` and `deliver_by` to `Order` itself so
delay metrics do not have to join.

**1.2 Vehicle master extension** — `body_type`, `manufacture_year`, `temperature_control`,
`reefer_make`, `reefer_min_c`, `reefer_max_c`, `iot_device_id`, `contract_start`, `contract_end`,
`contract_rate`, plus a `MaintenanceHistory` view endpoint aggregating work orders, schedules and
repair expenses per vehicle.

**1.3 Tyre management** — `Tyre` (serial, brand, pattern, size, purchase date, cost, expected km,
status) and `TyreFitment` (tyre, vehicle, position, fitted at km/date, removed at km/date, reason).
Feeds §26 and the tyre cost head in §8.

**1.4 Generic documents** — extend `ComplianceDocument` with nullable `vendor`, `order`, `trip`
and `customer` FKs (or move to a `GenericForeignKey`; recommend explicit FKs for query clarity),
add `document_category`, and add real file upload by lifting the pattern from
`backend/voucher_portal/storage.py`. Add `TRIP_DOCUMENT_TYPES` for delivery documents and
temperature reports.

### Phase 2 — Vendor hire commercials · ~2 weeks · ⚡ · ✅ Built (PR #20)

The highest-value gap. This is what makes margin computable.

**2.1 `VehicleHire`** — one per outside-sourced trip: `order`, `trip`, `vendor`, `hire_type`
(contract / spot), `vehicle_number`, `vehicle_type`, `capacity_kg`, `temperature_control`,
`driver_name`, `driver_phone`, `driver_licence`, `gps_available`, `agreed_rate`, `rate_basis`
(trip / km / day / ton / other), `loading_charge`, `unloading_charge`, `detention_rate_per_day`,
`detention_free_hours`, `toll_responsibility` (ours / vendor), `other_charges`,
`advance_amount`, `payment_terms_days`, `status`. Documents attach via 1.4.

**2.2 Vendor settlement service** — `vendor_payable(hire)` returning agreed rate + extra km +
detention + toll + loading/unloading + approved extras − advance − deductions − TDS, mirroring the
structure of `build_invoice_from_order`. Add `VendorBill.hire` FK so a bill is traceable to the
trip it pays for, and a `POST /hires/{id}/raise-bill/` that builds the bill from the computed
payable and posts it to the ledger.

**2.3 Margin on the trip** — with 2.1 and 2.2 in place, `order_profitability` gains a vendor-cost
line and can finally report own-versus-hired margin. Add `GET /orders/{id}/settlement/` returning
the four-sided sheet from §20: customer (billed, received, outstanding), vendor (payable, advance,
balance, status), driver (advance, allowance, incentive, deduction, net), vehicle (fuel, toll,
maintenance, other) with actual profit and settlement status.

**2.4 Vendor confirmation email** — the §6 template, rendered from order + requirement + hire +
allocation, queued through the Phase 0 outbox, with `POST /hires/{id}/resend-confirmation/`.

### Phase 3 — GPS, telematics and cold chain · ~3 weeks

**3.1 Device layer** — `GpsDevice` (vehicle, provider, device id, sim, install date, status,
last seen) and a provider-adapter interface with one concrete adapter first. Two ingest paths:
a webhook endpoint for push providers, and a Celery beat poller for pull APIs. Design the adapter
so old and new vehicles on different providers coexist, as §4 requires.

**3.2 `VehiclePosition`** — time-series of vehicle, timestamp, lat/lng, speed, heading, ignition,
odometer, with a partial index on `(vehicle, -recorded_at)` and a retention/rollup policy (raw for
90 days, hourly summaries beyond). Denormalise `last_position` onto `Vehicle` for the fast path
that §3 and §27 need.

**3.3 Derived movement metrics** — a periodic task computing per-trip running time, idle time
(ignition on, speed zero), stoppage time, GPS distance, and per-day rollups into a
`MovementSummary`. This is §16 in full, and it also gives §13 the GPS-km-versus-fuel-km
reconciliation and pilferage signal.

**3.4 Route and geofence intelligence** — store a planned route polyline on the trip; a task
compares live positions against it and raises a deviation when off-corridor beyond a threshold.
Automatic `GeofenceEvent` (zone, vehicle, trip, entry/exit, timestamp) using the existing
`Zone.contains()`, giving arrival and departure times for free. ETA from remaining distance and a
rolling average speed for the lane.

**3.5 Cold chain (§14, entirely new)** — `TemperatureReading` (vehicle, trip, device, timestamp,
set point, actual, reefer on/off, door open/closed) fed by the same ingest layer, plus
`TemperatureExcursion` (trip, started, ended, min/max, duration, threshold breached,
acknowledged by) raised by a detector task. Loading and delivery temperatures captured on the
ePOD. A temperature report per trip, exportable as PDF via the existing `reportlab` dependency
and attachable as a trip document.

### Phase 4 — Availability, recommendation and allocation · ~3 weeks · ⚡ · ✅ Built (PR #20)

The heart of the spec, and deterministic — no ML required.

**4.1 Availability projection** — a service computing, per vehicle: current status, current
position and place, current trip and destination, `available_from` (trip ETA + unload buffer) and
`available_at_place`, plus driver availability and document validity.
`GET /vehicles/availability/?place=&radius_km=&from=&to=` answers both "available now here" and
"available shortly nearby" — §3, §16 and §25 in one endpoint.

**4.2 Candidate search** — given an order's `VehicleRequirement`, gather own vehicles idle at
origin, own vehicles idle nearby, own vehicles becoming free within the window, own vehicles
returning toward origin, and vendor vehicles from `Fleet`/`Vendor` records — filtered by hard
constraints (capacity, type, temperature class, driver, document validity).

**4.3 Scoring** — for each candidate: `dead_km` (current or projected position → origin),
`fuel_cost` (dead + laden km × cost/km from Phase 5's cost model), `toll_estimate`,
`driver_cost`, `vendor_rate` where applicable, `expected_revenue` from the rate card,
`expected_profit`, plus a fit score on reporting-time feasibility and requirement match. Rank by
expected profit with a configurable dead-km ceiling — the spec is explicit that nearest ≠ best.

**4.4 Endpoint and UI** — `POST /orders/{id}/recommend-vehicles/` returning the ranked comparison
table from §9 (vehicle, source, dead km, cost, revenue, expected profit, verdict), rendered as an
allocation panel in the console. Then `POST /orders/{id}/confirm-vehicle/`: sets status, links
vehicle/driver/vendor, creates the trip, creates the `VehicleHire` when outside-sourced, runs the
document-validity check, and queues the vendor email — the whole of §6 in one transaction.

*This phase delivers §3, §5, §6, §16 and the operational substance of §23 and §25.*

### Phase 5 — Costing and billing depth · ~2 weeks

**5.1 `VehicleCostModel`** — per vehicle or vehicle class: `fuel_cost_per_km` (from history, as
`running_cost` already does), `toll_per_km`, `driver_allowance_per_day`, `tyre_cost_per_km`,
`maintenance_cost_per_km`, `depreciation_per_day`, `insurance_per_day`, `permit_per_trip`,
`overhead_per_trip`. Replaces the single blended `on_road_cost_per_km` in `project_lane` with the
itemised heads §8 lists, and brings fixed costs into the picture for the first time.

**5.2 Freight recommendation service** — `recommend_freight(requirement, candidate, target_margin)`
returning minimum freight (total cost, zero margin), recommended freight (cost ÷ (1 − target
margin)), expected revenue from the rate card, estimated cost and expected profit — the ladder
§8 asks for. Add `contract_rate_locked` on the order to skip cost analysis while still tracking
the trip, per §8.

**5.3 Invoice line items** — an `InvoiceLine` model (description, charge type, quantity, rate,
amount, taxable, HSN/SAC) and extend `build_invoice_from_order` to emit lines: base freight,
extra kilometres beyond planned, detention computed from arrival and departure timestamps
(available once Phase 3 lands), additional stops beyond the requirement, loading and unloading,
and other approved charges. §18 becomes evidence-backed rather than a typed-in lump.

**5.4 Expense posting** — post approved `TripExpense` and `FuelEntry` records to the ledger
through the existing `accounting.services` pattern, closing the §12/§20 gap so ledger-derived
profitability is complete.

**5.5 Driver settlement service** — extend `Settlement` with `trip_allowance`, `daily_allowance`,
`incentive`, `overtime`, `deductions`, `other_payments`, and compute the net from trips run, days
out and advances drawn rather than accepting typed figures (§11).

### Phase 6 — Alerts, MIS and control tower · ~3 weeks

**6.1 Alert engine** — `AlertRule` (type, scope, threshold, channels, recipients by role or user,
active) and `Alert` (rule, severity, subject, body, related object, raised at, acknowledged by
and at, resolved at). Evaluators run on Celery beat for scheduled checks (document expiry,
maintenance due, POD pending, billing pending, vendor payment due, driver settlement pending,
customer payment due) and on events for live ones (route deviation, excessive idling, temperature
excursion, reefer off, door open, breakdown, trip delay, vehicle available). Delivery through the
Phase 0 outbox — email and in-app first, SMS/WhatsApp behind the same interface. All 21 alert
types in §22 are covered by these two mechanisms.

**6.2 MIS reports** — customer-wise, route-wise, vendor-wise and trip-wise profitability, and an
own-versus-vendor comparison, all derived from the unified trip and the Phase 2 hire records.
Add delayed trips, pending POD, route deviations, excessive idling, available-by-location and
future-availability to the dashboard payload.

**6.3 Control tower (§27)** — a `GET /control-tower/` endpoint composing live vehicle positions,
active trips, available and idle vehicles, vendor vehicles, delayed vehicles, future availability,
temperature alerts, route deviations, pending deliveries, POD pending and payment pending; and a
single console screen with a map, alert rail and status tiles. Drill-down is a URL scheme —
company → location → vehicle → trip → order → expense → billing → payment → settlement — with each
level a filtered view of an existing endpoint, so it composes rather than duplicates.

### Phase 7 — Learned estimators · ~4 weeks, after 6–12 months of trip history

Deliberately last. Each of these replaces a deterministic estimator from an earlier phase with a
learned one, behind the same interface, so the system degrades gracefully to Phase 4/5 behaviour
when data is thin.

- **§24 freight recommendation** — quantile regression on lane, vehicle type, distance, season,
  diesel price and historical acceptance, producing minimum/recommended freight and a maximum
  acceptable dead km. Needs a completed-trip feature store (revenue, actual cost, dead km,
  duration, customer, lane, vehicle class) built from Phase 2's settlement records.
- **§25 predictive availability** — learned dwell times at loading, unloading and rest stops
  replacing the fixed buffers in 4.1, improving ETA and therefore allocation quality.
- **§26 predictive maintenance** — failure-risk scoring per component from km, engine hours,
  repair history, tyre wear and breakdown history, layered over the existing interval schedules
  rather than replacing them.
- **§23 allocation optimiser** — the Phase 4 scorer with learned cost and duration estimates, and
  optionally a multi-order assignment solver when several orders compete for the same vehicles.
  That solver is now specified in its own right — heterogeneous dry/reefer fleet, third-party
  hire as a priced outsourcing decision, GPS start positions and re-planning — in
  [DISPATCH-PLANNING.md](DISPATCH-PLANNING.md). It does not need the learned estimators here: it
  is deterministic, and consumes Phases 1, 3 and 5 as inputs.

*Prerequisite:* a `TripOutcome` feature store written on trip settlement. Worth adding in Phase 2
even though nothing reads it until Phase 7, so history accumulates from the moment margins become
computable.

---

## Summary

| Phase | Delivers | Weeks |
|-------|----------|-------|
| 0 | Unified trip spine, ownership types, status history, task queue, outbox | 2 |
| 1 | Vehicle requirement capture, vehicle/tyre/document master depth | 2 |
| 2 | Vendor hire commercials, vendor settlement, margin, confirmation email | 2 |
| 3 | GPS integration, movement metrics, geofencing, route deviation, cold chain | 3 |
| 4 | Availability, nearby search, recommendation, allocation confirmation | 3 |
| 5 | Itemised cost model, freight recommendation, invoice lines, driver settlement | 2 |
| 6 | Alert engine, MIS reports, control tower | 3 |
| 7 | Learned estimators for freight, availability, maintenance, allocation | 4 |

**~17 weeks to a complete deterministic O2A-to-settlement system** (Phases 0–6), with Phase 7
following once there is history to learn from.

If the goal is the shortest path to the spec's central claim — *"a complete O2A-to-Settlement
Transport Management System, rather than simply a vehicle tracking application"* — Phases 0, 2 and
4 are the ones that get there. Phase 0 makes one trip carry one cost stack, Phase 2 makes the
margin on a hired truck computable, and Phase 4 turns allocation from a dispatcher's guess into a
ranked, costed recommendation. Roughly seven weeks, and after them the system distinguishes own
from attached from outside-sourced capacity and reports actual profit per trip — which is the
line between the two things the spec contrasts.
