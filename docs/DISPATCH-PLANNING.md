# Dispatch planning — CVRP module implementation plan

## Implementation update

A first slice of Phases A–C is built on branch `claude/cvrp-dispatch-planning-ewm262`:
a `dispatch` Django app (`DispatchPlan`, `DispatchTask`, `PlanVehicle`, `PlannedRoute`,
`PlannedStop`, `PlanEvent`, `HireRequirement`, `TravelMatrixEntry`), a dependency-free
greedy solver (`dispatch/solver/greedy.py` — cluster-by-shared-pickup, cheapest sequential
insertion, temperature compatibility, capacity, soft time windows, the own-vs-outsource
disjunction), and `collect` / `solve` / `commit` / `readiness` / `explain` endpoints under
`/api/v1/dispatch/`. Commit lands orders on a real `Trip` and moves the vehicle to
`allocated`, converting an indent to an order inline where needed. `Vehicle` gained
`volume_cbm`, `temperature_class`, `body_type`, reefer fields and a `home_place`. The
three defects in §3 below are fixed. `dispatch.view` / `dispatch.plan` / `dispatch.commit`
permissions are seeded on the Dispatcher and Branch manager roles. 18 new tests, 352
backend tests pass overall. A minimal **Planning** console page lists plans and drives
collect/solve/commit from a detail drawer.

**Scope taken, stated plainly:**
- The greedy solver clusters tasks by shared pickup and serves one cluster's stops in
  full before starting the next (§6.10's stated limitation) — it does not interleave
  pickups from different origins mid-route. OR-Tools (§6.10, §17) is not wired in; the
  `solver="ortools"` field exists on `DispatchPlan` and falls back to greedy when the
  module import fails, so it is a drop-in addition later.
- Own, attached and leased `fleet.Vehicle` rows become `PlanVehicle` candidates.
  Genuinely spot capacity (§6.7's second mechanism) is not pre-loaded as a vehicle; it is
  priced as `outsource_estimate` per task and surfaces as a `HireRequirement` when the
  solver drops a task, exactly as designed.
- GPS is read from `Vehicle.current_latitude/longitude` as-is (§7.1's freshness flag is
  implemented as `PlanVehicle.position_stale`); the live position time-series, geofence
  arrival capture and re-planning in §7.2–7.3 are not built.
- India-specific constraints (§9 — city no-entry hours, e-way bill validity, border
  dwell) and the console's Gantt/map board (§12) are not built; the shipped console is a
  list-and-drawer view proportionate to this pass.
- Vendor RFQ (`request-quotes`) and manual `award` exist as a minimal version of §8 —
  award requires the requirement's tasks to already carry a linked order (i.e. the
  underlying indent has been converted), rather than the full `CarrierOffer` quote
  workflow.

What follows is the original design in full, unedited, as the reference for what is not
yet built.


A plan for turning today's one-order-at-a-time allocation desk into a **dispatch planning
module**: the dispatcher loads tomorrow's demand, the system produces a costed, feasible set
of routes across own dry vehicles, own reefers and hired third-party capacity, and the
dispatcher commits it in one action.

The optimisation core is a capacitated vehicle routing problem (CVRP), extended with the
constraints this fleet actually has. Everything below is grounded in what is already in this
repository — every "exists" claim points at a file, and every gap carries a design.

Read alongside [O2A-GAP-ANALYSIS.md](O2A-GAP-ANALYSIS.md): this module is the built-out form
of that plan's Phase 7 aside — *"optionally a multi-order assignment solver when several orders
compete for the same vehicles"* — and it consumes Phases 1, 3 and 5 as inputs.

---

## 1. What the fleet operator needs

The operator runs own dry vehicles, own reefers (chiller and frozen), and hires trucks from
third-party transport owners when own capacity runs out or a lane is cheaper bought than run.
Every own vehicle carries a GPS device. The dispatch planning module has to cover all of it:

| # | Capability | Where it lands |
|---|-----------|----------------|
| 1 | Collect tomorrow's demand from indents and booked orders into one planning board | §5.1 `DispatchTask` |
| 2 | Plan FTL point-to-point moves and multi-drop distribution runs in the same run | §6.3 PDPTW + CVRP nodes |
| 3 | Respect weight **and** volume capacity per vehicle | §6.2 capacity dimensions |
| 4 | Never put frozen cargo in a dry body; never mix incompatible temperature classes | §6.4 |
| 5 | Honour reefer set points, pre-cooling time and reefer running cost per hour | §6.4, §6.6 |
| 6 | Honour customer time windows, plant loading hours and city no-entry hours | §6.5, §9 |
| 7 | Start each vehicle from its **live GPS position**, not a notional depot | §7.1 |
| 8 | Include vehicles that are not free yet but will be, with a projected release time | §7.2 |
| 9 | Decide own-vs-hire per load on cost, not on habit | §6.7 |
| 10 | Raise hire requirements, collect vendor quotes and convert an accepted quote to a hire | §8 |
| 11 | Keep hired vehicles inside the same plan, with their own cost and constraints | §6.7, §8.3 |
| 12 | Block a vehicle whose insurance, permit, fitness or PUC has lapsed | §6.8 |
| 13 | Block a driver whose licence has lapsed, and respect driving-hour limits and rest | §6.8, §6.5 |
| 14 | Show a costed plan: distance, time, utilisation, cost, revenue, margin per route | §5.3, §11 |
| 15 | Let the dispatcher override any assignment and immediately re-cost it | §10.2 |
| 16 | Commit the plan into orders, trips, waypoint sequences and vehicle hires | §10 |
| 17 | Track plan versus actual from GPS and re-plan the remainder of the day | §7.3 |
| 18 | Explain why each load went where — an audit trail, not a black box | §11 `explain` |
| 19 | Scope planning by branch/depot, with role-gated commit rights | §12 |
| 20 | Report planner KPIs: fill rate, dead km, on-time, own-vs-hire mix, cost per tonne-km | §11, §13 |

Items 1–20 are the scope of this document. Nothing here needs machine learning; the whole
module is deterministic, which is what makes it auditable and testable.

---

## 2. What already exists

Real assets this module builds on, not around:

| Asset | Where | What it gives the planner |
|-------|-------|---------------------------|
| Single-order vehicle scorer | `backend/fleet/allocation.py:99` | The cost/revenue/profit vocabulary and the own-vs-vendor comparison, already ranked by expected profit |
| Per-km cost from real history | `backend/fleet/billing.py:79` `running_cost()` | Diesel price and mileage learned from `FuelEntry`, on-road cost from `TripExpense` |
| Vendor rate estimation | `backend/fleet/allocation.py:51` | Average agreed rate per vendor from `VehicleHire` history, flagged when it falls back to a markup |
| Hire commercials | `backend/fleet/models.py:964` `VehicleHire` | Agreed rate and basis, detention, toll responsibility, advance, `gps_available` |
| Vehicle status vocabulary and log | `backend/fleet/models.py:42`, `:99` | 12 states, `set_vehicle_status()`, `VehicleStatusLog`, `expected_available_at` |
| Live-ish position on the vehicle | `backend/fleet/models.py:65` | `current_latitude` / `current_longitude` / `gps_device_id` |
| Geocoded network | `backend/fleet/models.py:337`, `:312` | `Place` with lat/lng, `Zone.contains()` geofencing, `ServiceArea` |
| Multi-stop data structure | `backend/fleet/models.py:589` `Waypoint` | Ordered stops per order — sequence exists, nothing computes it |
| One trip, many orders | `backend/fleet/models.py:486` | `Order.trip` is an FK with `related_name="orders"`, so a consolidated route already has a commit target |
| Straight-line distance | `backend/fleet/models.py:13` `haversine_km()` | A working fallback matrix on day one |
| Availability endpoint | `backend/fleet/views.py:104` | Vehicles near a place with a document-expiry flag |
| Allocation commit | `backend/fleet/views.py:667` `confirm-vehicle` | Links vehicle/driver/vendor, opens the trip, raises the hire, mails the vendor — the per-route commit step, already written |
| Recorded, resendable outbox | `backend/iam/messaging.py` | The channel for hire RFQs and confirmations |
| Permission catalogue | `backend/iam/models.py:23` | Where `dispatch.*` codes get added |

**The commit path is the pleasant surprise.** Because `Order.trip` is a foreign key rather than
a one-to-one, a planned route carrying six consignments commits to one `Trip` with six `Order`
rows pointing at it, and `Waypoint` already stores the stop order. No schema change is needed to
*land* a plan — only to *produce* one.

## 3. What is missing, and three things that are wrong

**Missing outright:** any notion of a planning run; volume capacity; a temperature class on the
vehicle; road distance and travel time (only straight-line exists); time windows on a stop;
driver duty limits; a solver; a position time-series; a way to ask a vendor for capacity rather
than record capacity already agreed.

Three defects that a planner would expose on day one, worth fixing inside this work:

**3.1 Consolidated trips under-report freight.** `Trip.settlement_summary()`
(`backend/fleet/models.py:171`) prices a trip from `self.orders.first()`:

```python
order = self.orders.first()
freight = money(order.total_amount) if order else money(self.freight_amount)
```

With one order per trip that is correct. The moment a planned route carries six orders, five of
them vanish from trip profit. Fix: sum `total_amount` across `self.orders`, falling back to
`freight_amount` only when the trip has no orders at all.

**3.2 The candidate pool ignores vehicles that are about to be free.**
`FREE_VEHICLE_STATUSES = ("available", "idle")` (`backend/fleet/allocation.py:15`) excludes a
truck unloading 40 km from tomorrow's pickup that will be free at 18:00 today. `Vehicle.
expected_available_at` already exists and is unused by the scorer. A day-ahead planner that
cannot see tomorrow's capacity is planning against a fictitious fleet.

**3.3 Cost basis is recomputed per candidate.** `_own_candidate()` calls
`running_cost(vehicle=vehicle)` inside the candidate loop — two aggregate queries per vehicle.
At 1000 vehicles that is 2000 queries before the solver starts. The planner must compute one
cost basis per vehicle **class** up front (§6.6).

**3.4 Geodata is optional.** `Place.latitude` / `longitude` are nullable
(`backend/fleet/models.py:349`) and `Order.distance_km` is a typed-in decimal. A CVRP over
places without coordinates is not a degraded plan, it is no plan. §14 makes this a hard gate
with a readiness endpoint rather than a runtime surprise.

---

## 4. The problem, stated properly

Textbook CVRP is *n* customers with demands, one depot, identical vehicles, minimise distance.
None of those four assumptions hold here. The problem this fleet actually has is a
**heterogeneous multi-depot pickup-and-delivery problem with time windows, temperature
compatibility, and an outsourcing option** — in the literature, HFVRPTW + PDPTW + VRPPC
(*Vehicle Routing Problem with Private fleet and Common carrier*).

Concretely, per planning run:

- **Nodes** — each task contributes a pickup node and a delivery node (FTL and multi-drop are
  the same shape; a distribution run is simply many deliveries sharing one pickup).
- **Vehicles** — heterogeneous in weight capacity, volume, temperature class, cost per km, cost
  per hour, fixed cost, start position, start time and allowed zones. Own, attached and hired
  vehicles are all vehicles; they differ only in their cost parameters and constraints.
- **Depots** — none, in the classical sense. Every vehicle starts at its own last known GPS
  position and ends either at its home branch, at its final delivery (open route), or at a
  named repositioning point.
- **Outsourcing** — every task may be left unserved by the own fleet at a penalty equal to what
  the spot market would charge for it. The solver outsources exactly when that is cheaper.
- **Objective** — maximise contribution: served revenue − running cost − time cost − fixed cost
  of used vehicles − hire cost, with lexicographic tie-breaks on service level and balance.

The academic naming matters for one practical reason: each of those variants has a known
encoding in OR-Tools' routing library, so this is an assembly job, not a research project.

---

## 5. Data model

New Django app `backend/dispatch/`, not new tables in `fleet`. The planner reads `fleet` and
writes plans; keeping it a separate app keeps the dependency one-directional and keeps
`fleet/models.py` (1005 lines) from growing another third.

### 5.1 Demand

**`DispatchTask`** — the unit of demand handed to the solver, derived from an `Indent` or an
`Order` and re-derivable, never hand-maintained.

```
plan            FK DispatchPlan
order           FK fleet.Order       null   # one of order/indent is set
indent          FK fleet.Indent      null
task_type       ftl | multi_drop_leg | pickup_only | delivery_only | reposition
pickup          FK fleet.Place
dropoff         FK fleet.Place
weight_kg       Decimal
volume_cbm      Decimal
packages        int
temperature_class   dry | chiller | frozen        # required class, not the vehicle's
temp_set_point_c    Decimal null
pickup_window_start / pickup_window_end       DateTime
drop_window_start  / drop_window_end          DateTime
pickup_service_minutes / drop_service_minutes int    # loading and unloading dwell
priority        must_go | normal | deferrable
allowed_vehicle_types  JSON list             # customer-mandated body types
banned_vehicles        JSON list             # customer blacklist, driver blacklist
revenue_estimate       Decimal               # from the rate card, for the objective
outsource_estimate     Decimal               # spot cost, the disjunction penalty
status          pending | planned | outsourced | dropped | committed
drop_reason     CharField
```

`temperature_class` deliberately mirrors `LOAD_TYPES` in `backend/fleet/models.py:140`
(`dry / chiller / frozen`), which today exists only as a trip-settlement field. This is where it
becomes an operational constraint.

### 5.2 Supply

Extend `fleet.Vehicle` rather than shadow it — one row per truck stays one row per truck:

```
volume_cbm             Decimal        # missing today; capacity_kg exists alone
temperature_class      dry | chiller | frozen | multi
reefer_min_c / reefer_max_c   Decimal
reefer_fuel_lph        Decimal        # reefer diesel burn per hour, default 2.5
body_type              open | closed | container | tanker | flatbed | reefer
home_branch            FK iam.Branch
home_place             FK fleet.Place
average_speed_kph      Decimal        # per-vehicle fallback when the matrix is thin
max_drive_hours_per_day  Decimal      # operator policy, default 10
```

**`VehicleCompartment`** — for a bulkheaded reefer running chiller and frozen together:
`vehicle`, `name`, `temperature_class`, `capacity_kg`, `capacity_cbm`. A vehicle with no
compartment rows is single-compartment, capacity taken from the vehicle.

**`PlanVehicle`** — a vehicle's *offer* into one planning run, snapshotted so a plan stays
reproducible after the fleet moves on:

```
plan, vehicle (null for a not-yet-identified hire), driver (null)
source          own | attached | leased | hired | spot_slot
vendor          FK fleet.Vendor null
start_latitude / start_longitude / start_place
available_from  DateTime            # now, or expected_available_at
must_return_to  FK fleet.Place null # null means an open route
capacity_kg / capacity_cbm / temperature_class      # snapshot
cost_per_km / cost_per_hour / fixed_cost            # snapshot, see §6.6
max_stops / max_route_km / max_duty_minutes
locked_to_route  bool               # dispatcher pinned this vehicle
excluded         bool + reason      # document expired, in workshop, driver unavailable
```

### 5.3 The plan

**`DispatchPlan`** — one planning run: `code`, `branch`, `plan_date`, `horizon_hours`,
`status` (`draft | ready | solving | solved | failed | committed | superseded`), `objective`
(JSON weights), `solver` (`ortools | greedy`), `solver_seconds`, `solver_status`,
`parent_plan` (for a re-plan), `created_by`, `committed_at`, `committed_by`, and a `summary`
JSON holding the KPI block from §11.

**`PlannedRoute`** — one vehicle's day: `plan`, `plan_vehicle`, `sequence`, `total_distance_km`,
`total_duration_minutes`, `drive_minutes`, `wait_minutes`, `dead_km`, `max_load_kg`,
`utilisation_weight_percent`, `utilisation_volume_percent`, `estimated_cost`,
`estimated_revenue`, `estimated_margin`, `temperature_class`, `feasible`, `violations` (JSON),
`locked`, `committed_trip` FK.

**`PlannedStop`** — `route`, `sequence`, `task`, `place`, `stop_type` (`pickup | drop | rest |
return`), `planned_arrival`, `planned_departure`, `service_minutes`, `wait_minutes`,
`load_after_kg`, `load_after_cbm`, `distance_from_previous_km`, `locked`, `actual_arrival`,
`actual_departure`, `variance_minutes`.

**`PlanEvent`** — append-only: created, solved, task dropped with reason, manual move, hire
requested, quote accepted, committed, re-planned. This is item 18's audit trail and it is also
how a support engineer reconstructs "why did truck 9182 go to Bhiwandi".

### 5.4 Distance and time

**`TravelMatrixEntry`** — `origin_key`, `destination_key` (place id, or lat/lng rounded to
4 decimal places ≈ 11 m), `distance_km`, `duration_minutes`, `provider`
(`haversine | osrm | google | learned`), `vehicle_class`, `time_bucket` (hour-of-day band),
`fetched_at`, `hit_count`. Unique on `(origin_key, destination_key, provider, vehicle_class,
time_bucket)`. See §6.9.

### 5.5 Hire

**`HireRequirement`** — capacity the plan needs but the own fleet cannot supply: `plan`,
`tasks` (M2M), `vehicle_type`, `temperature_class`, `capacity_kg`, `pickup`, `dropoff`,
`report_by`, `estimated_cost`, `status` (`open | quoted | awarded | cancelled`).

**`CarrierOffer`** — a vendor's response: `requirement`, `vendor`, `offered_rate`, `rate_basis`,
`vehicle_number`, `vehicle_type`, `temperature_class`, `gps_available`, `driver_name`,
`driver_phone`, `valid_until`, `status` (`invited | quoted | accepted | rejected | expired`),
`responded_at`. Accepting one creates the existing `fleet.VehicleHire` — no parallel commercial
model.

---

## 6. The solver

Package `backend/dispatch/solver/`:

```
inputs.py    collect tasks and PlanVehicles, snapshot cost, validate readiness
matrix.py    distance/duration providers + cache (§6.9)
costing.py   per-vehicle-class cost basis, reefer hourly cost, hire cost (§6.6)
model.py     build the OR-Tools routing model — dimensions and constraints
search.py    first-solution strategy, metaheuristic, time limit, warm start
greedy.py    savings + 2-opt fallback, dependency-free (§6.10)
extract.py   assignment -> PlannedRoute / PlannedStop / dropped tasks
explain.py   per-task rationale (§11)
```

### 6.1 Scaling

OR-Tools routing works in integers. Fix the units once, in `inputs.py`, and never convert
elsewhere: **distance in metres, time in seconds, weight in kg, volume in litres, money in
paise**. Every cost callback returns paise. Rounding is round-half-up at the boundary, matching
`money()` in `backend/fleet/models.py:10`.

### 6.2 Capacity

Two dimensions with per-vehicle capacity:

```python
routing.AddDimensionWithVehicleCapacity(weight_cb, 0, weight_caps, True, "Weight")
routing.AddDimensionWithVehicleCapacity(volume_cb, 0, volume_caps, True, "Volume")
```

Demand is positive at a pickup node and negative at its delivery node, so the cumulative is the
live load and a vehicle can pick up again after dropping — which is what makes multi-drop and
backhaul fall out for free rather than needing a special case.

### 6.3 Pickup and delivery

For each task, the two nodes are bound together:

```python
routing.AddPickupAndDelivery(p, d)
solver.Add(routing.VehicleVar(p) == routing.VehicleVar(d))
solver.Add(time_dim.CumulVar(p) <= time_dim.CumulVar(d))
```

A pure distribution run — one plant, forty shops — is modelled with a single shared pickup node
visited once, or (simpler and usually better) as delivery-only nodes on vehicles whose start
node *is* the plant. Both are supported; `task_type` selects.

### 6.4 Temperature compatibility

This is the constraint the operator cares most about and the one most CVRP write-ups skip.

**Vehicle–task compatibility** is a hard filter on the node's vehicle variable:

```python
routing.VehicleVar(node).SetValues(compatible_vehicle_indices(task))
```

Compatibility rules, as policy rather than folklore:

- `frozen` cargo → only vehicles whose class is `frozen` or `multi` with a frozen compartment.
- `chiller` cargo → `chiller`, `frozen` (a frozen unit holds a chiller set point) or `multi`.
- `dry` cargo → any vehicle, *unless* `DISPATCH_ALLOW_DRY_IN_REEFER` is off, which some
  operators require for hygiene and odour reasons. Default: allowed, but §6.6 charges it
  nothing for the reefer unit since it runs off.

**Mixed loads on one route.** A single-compartment body must not carry frozen and dry on the
same leg. Encode with one counting dimension per class and a reified constraint per vehicle:

```python
for cls in ("dry", "chiller", "frozen"):
    routing.AddDimension(count_cb[cls], 0, len(nodes), True, f"Class_{cls}")
for v in range(num_vehicles):
    end = routing.End(v)
    distinct = solver.Sum([routing.GetDimensionOrDie(f"Class_{c}").CumulVar(end) > 0
                           for c in classes])
    solver.Add(distinct <= compartments_of[v])
```

`compartments_of[v]` is 1 for a plain body and the `VehicleCompartment` count for a bulkheaded
reefer, so multi-temperature vehicles get the freedom they physically have and no more. When a
vehicle is compartmented, weight capacity becomes per class: one weight dimension per class,
each capped at that compartment's capacity.

**Pre-cooling.** A reefer must run down to set point before loading. `pickup_service_minutes`
for a chiller task gets `+ DISPATCH_PRECOOL_MINUTES` (default 45) when the vehicle's previous
stop was not already at that class. Cheap to model, and it is the difference between a plan that
works on paper and one that works at the plant gate.

### 6.5 Time

One time dimension with slack (slack = permitted waiting):

```python
routing.AddDimension(time_cb, max_wait_seconds, max_route_seconds, False, "Time")
```

- Node windows: `time_dim.CumulVar(node).SetRange(open, close)` from the task's window,
  intersected with `Place.loading_hours` (`backend/fleet/models.py:353`, today a free-text
  `"09:00-18:00"` — parse it, and add structured `open_time`/`close_time` fields alongside).
- Vehicle start: `time_dim.CumulVar(routing.Start(v)).SetMin(available_from)` — §7.2.
- Driving-hour limit: a second `Drive` dimension counting only transit, capped at
  `max_drive_hours_per_day`, so waiting at a plant does not consume the driver's legal day.
- Rest: `time_dim.SetBreakIntervalsOfVehicle(breaks, v, node_visit_transits)` for a mandated
  break after N driving hours, and for the overnight break on a multi-day route.
- Soft windows where the customer's window is a preference rather than a contract:
  `time_dim.SetCumulVarSoftUpperBound(node, preferred_end, penalty_per_second)`.

### 6.6 Cost

Per **vehicle class**, computed once per plan (fixing §3.3), not per candidate:

```
cost_per_km   = running_cost(class).fuel_cost_per_km + on_road_cost_per_km + tyre + maintenance
cost_per_hour = driver_cost_per_hour
                + reefer_fuel_lph × diesel_price     # only while the unit runs
fixed_cost    = permit/trip + overhead/trip + driver bhatta for the day
```

`running_cost()` (`backend/fleet/billing.py:79`) already learns diesel price and mileage from
`FuelEntry` and blends on-road spend from `TripExpense`; the itemised heads come from the gap
analysis's Phase 5.1 `VehicleCostModel` if it has landed, and from the blended figure if not.

**The reefer cost point is the one that a distance-only objective gets wrong.** A reefer unit
burns roughly 2–3 litres an hour whether the truck is moving, waiting at a dock or parked
overnight with cargo aboard. A plan that minimises kilometres will happily leave a loaded reefer
idling for five hours to hit a delivery window and call it efficient. Charging time as well as
distance — and charging it only for the classes that need cooling — is what makes the plan's
margin the real one. The arc cost is therefore:

```python
arc_cost(from, to, v) = km(from,to) × cost_per_km[v] + seconds(from,to) × cost_per_second[v]
routing.SetArcCostEvaluatorOfVehicle(cb, v)
routing.SetFixedCostOfVehicle(fixed_cost[v], v)
```

with waiting time picked up through the time dimension's soft costs so idling is not free.

### 6.7 Own versus hired — the outsourcing decision

Two mechanisms, used together:

**Contracted or already-offered capacity is a vehicle.** An attached truck, or a vendor truck
whose rate is known from `VehicleHire` history, enters as a `PlanVehicle` with
`source=hired`, `fixed_cost` = the agreed trip rate (or `cost_per_km` = the per-km rate),
`cost_per_hour` = 0 (their driver, their problem), and a start position at the vendor's yard or
the pickup point. The solver then treats own-versus-hired as a plain cost comparison, which is
exactly the decision the operator is making.

**Genuinely spot capacity is a disjunction penalty.** For every task:

```python
routing.AddDisjunction([p, d], outsource_penalty(task))
```

where `outsource_penalty` is the spot cost from `_vendor_rate_estimate()`
(`backend/fleet/allocation.py:51`) — the vendor's own hire history where it exists, a flagged
markup where it does not. Dropping the task is not "failing to serve it"; it is **buying it on
the market**, and the solver drops it precisely when own-fleet service would cost more than the
market price. A `must_go` task whose customer contract forbids subcontracting gets an infinite
penalty and cannot be dropped.

This dual encoding is the heart of the module. It means the plan output has three buckets —
served by own fleet, served by pre-arranged hire, send to market — and each is a priced decision
the dispatcher can see and argue with.

### 6.8 Eligibility filters

Applied in `inputs.py` before the model is built, each producing an `excluded` reason on the
`PlanVehicle` so the dispatcher sees *why* a truck is not in the plan:

- Vehicle status in `under_maintenance`, `breakdown`, `inactive`, `driver_unavailable`.
- Any `ComplianceDocument` for the vehicle expired, or expiring inside the plan horizon —
  reusing the check already in `confirm-vehicle` (`backend/fleet/views.py:712`).
- A `MaintenanceSchedule` that comes due inside the planned kilometres (`is_due`,
  `km_remaining` already exist at `backend/fleet/models.py:892`).
- Driver licence expired, or driver already committed to another route in this plan.
- Zone restrictions: a vehicle banned from a `Zone` cannot take a node inside it
  (`Zone.contains()`, `backend/fleet/models.py:330`).

### 6.9 The travel matrix

Straight-line distance is not good enough to sequence stops — on Indian road networks the
detour factor swings between 1.2 and 1.9, and a plan built on haversine will promise arrival
times it cannot keep. Three providers behind one interface, chosen by setting:

| Provider | When | Notes |
|---------|------|-------|
| `haversine` | Default, always available | `haversine_km()` × a configurable detour factor (default 1.35) and an average-speed time. Good enough to *develop* against and to survive a provider outage. |
| `osrm` | Recommended for production | Self-hosted OSRM on an India OSM extract. One `/table` call returns an N×N matrix; no per-request cost, no rate limit, no data leaving the VPC. |
| `google` | Where road accuracy and live traffic matter more than cost | Distance Matrix API, batched 25×25, billed per element — which is why the cache below is not optional. |

`TravelMatrixEntry` caches every pair. A 60-stop plan is 3,600 pairs; the same depot–customer
pairs recur every single day, so a warm cache turns a 3,600-element request into a handful. TTL
is provider-dependent (30 days for `osrm`, 7 for `google`, infinite for `haversine`), and the
`learned` provider back-fills durations from actual GPS traces (§7.3) keyed by hour-of-day
bucket, so the matrix gets more honest about Mumbai at 18:00 the longer the system runs.

### 6.10 Search, and a fallback that needs no wheel

Default search: `PARALLEL_CHEAPEST_INSERTION` first solution, `GUIDED_LOCAL_SEARCH`
metaheuristic, time limit from plan size (§13). Warm-start a re-plan from the committed
assignment with `ReadAssignmentFromRoutes`, and pin executed stops with
`ApplyLocksToAllVehicles`.

`ortools` is a new binary dependency on a backend whose `requirements.txt` is currently eight
pure-Python-or-manylinux packages. The wheel is large (~100 MB) and the EC2 deployment cuts
releases with `scripts/deploy-fms.sh`. So the solver is pluggable via `DISPATCH_SOLVER`, and
`greedy.py` implements Clarke–Wright savings plus 2-opt and Or-opt local search with the same
constraint checks — perhaps 8–15% worse on cost, entirely dependency-free, and fast enough for
the 40-vehicle case. It is not a toy: it is the CI default, so the constraint logic is tested on
every run without pinning a 100 MB wheel into the test image, and it is the production fallback
if OR-Tools cannot be installed on a given host.

*Decision to confirm:* whether to take the `ortools` dependency at all. Recommend yes — the
quality gap is real at 100+ stops — but ship Phase 1 on `greedy` so the module is useful before
that decision is made.

---

## 7. GPS in the loop

The vehicles carry GPS devices, which is what makes this planner different from one that plans
from a spreadsheet. Three distinct uses, in dependency order:

### 7.1 Start positions

Each `PlanVehicle.start_latitude/longitude` comes from `Vehicle.current_latitude/longitude`
(`backend/fleet/models.py:65`) with a freshness check: a fix older than
`DISPATCH_MAX_FIX_AGE_MINUTES` (default 120) falls back to `current_place`, then to
`home_place`, and the plan flags the vehicle as `position_stale` so the dispatcher knows the
dead-km figure on that route is an estimate. Silent staleness is worse than a visible warning —
this is the same principle as the existing `estimated_cost: true` flag in
`backend/fleet/allocation.py:76`.

### 7.2 Projected availability

A vehicle mid-trip is still plannable for tomorrow. `available_from` is:

```
running / loaded          -> trip ETA from GPS progress + unload buffer + return leg if required
awaiting_unloading        -> now + unload buffer
allocated                 -> committed route's end time
available / idle          -> now
```

with `Vehicle.expected_available_at` as the override a dispatcher can type in. This fixes §3.2
and is what turns a *dispatch* tool into a *planning* tool: tomorrow's plan uses tomorrow's
fleet.

### 7.3 Plan versus actual, and re-planning

Once committed, each `PlannedStop` gets `actual_arrival` from a geofence crossing
(`Zone.contains()` against incoming positions, or a radius around the `Place`), and
`variance_minutes` is the plan's honesty metric. Three consequences:

- **Live ETA** for every downstream stop on the route, which feeds the tracking screen and the
  customer-facing `/track` page that already exists.
- **Re-plan triggers** — a route more than `DISPATCH_REPLAN_THRESHOLD_MINUTES` behind, a
  breakdown `Issue`, a cancelled order, or a new `must_go` indent. Re-planning creates a child
  `DispatchPlan` with `parent_plan` set, locks every stop already executed, and re-solves the
  tail. Nothing is ever silently rewritten under a driver who is already on the road.
- **Learned travel times** — completed legs write back into `TravelMatrixEntry` with
  `provider="learned"`, bucketed by hour of day. The plan gets better at rush hour without
  anyone tuning a parameter.

**Hired vehicles usually have no GPS.** `VehicleHire.gps_available`
(`backend/fleet/models.py:982`) already records this. For a hire without it, the module falls
back to driver phone check-in at each stop (an SMS or a link, through the existing outbox), and
marks that route's ETAs `unverified` so the control tower does not present a guess as a fix.

---

## 8. Third-party hire

### 8.1 The requirement comes out of the plan

After a solve, every task the solver chose to outsource (§6.7) is grouped by lane, temperature
class and time window into `HireRequirement` rows. This is the difference from today's flow: the
dispatcher does not decide to hire and then look for a truck; the plan tells them exactly what
capacity is missing and what it is worth paying.

### 8.2 Sourcing

`POST /dispatch-plans/{id}/request-quotes/` fans out an RFQ to vendors filtered by service area
and `vendor_type in (transporter, broker)`, through `iam/messaging.py` so every message is
recorded and resendable. Vendors reply by email or phone; the desk records a `CarrierOffer`.
A future vendor portal can let them self-serve — the model does not care which.

### 8.3 Award and re-plan

Accepting an offer creates a `fleet.VehicleHire` (the existing model, unchanged) and, if the
plan has not been committed, inserts the hired truck into the plan as a `PlanVehicle` with
`source=hired` and re-solves. A hired truck arriving cheap can pull work back off the market and
change the own-fleet routes around it — which is exactly the behaviour an operator wants and
cannot get from a spreadsheet.

Award is also where the existing `confirm-vehicle` machinery earns its keep: it already registers
an unknown vendor vehicle, opens the trip, raises the `VehicleHire`, checks documents and mails
the confirmation (`backend/fleet/views.py:667`).

---

## 9. India-specific constraints

Not optional extras — a plan that ignores these is wrong on the ground:

- **City no-entry hours.** Trucks are barred from most Indian metro cores during the day
  (typically 08:00–22:00, varying by city). Model as a time-window restriction on `Zone`:
  `no_entry_start` / `no_entry_end` / `applies_to_body_types`, enforced as a forbidden interval
  on any node inside that zone. This single constraint reorders most urban distribution plans.
- **E-way bill validity.** One day per 200 km (100 km for over-dimensional cargo). A multi-day
  route whose leg exceeds the validity of the e-way bill already on the order
  (`Order.eway_bill_number`) gets a plan warning, since extension is a compliance action, not a
  routing one.
- **State border and permit time.** A per-border dwell allowance in the matrix, keyed off
  `Place.state` changing between consecutive stops.
- **Driver bhatta by day, not by hour.** A route crossing midnight costs another day's
  allowance; this is a step function in `fixed_cost` and it changes whether an overnight halt or
  a driver change is cheaper.
- **Night driving.** Some customers and some cargo classes prohibit it; a per-task flag becomes
  a forbidden time interval.
- **GTA/RCM.** Untouched by the planner — revenue for the objective is taken pre-GST from
  `ServiceRate.quote()`, which already separates taxable value from tax
  (`backend/fleet/models.py:417`). Planning on a tax-inclusive number would systematically
  overvalue reverse-charge lanes.

---

## 10. Committing a plan

`POST /dispatch-plans/{id}/commit/`, one transaction, idempotent on `plan.status == committed`:

1. For each `PlannedRoute`: create one `Trip` (vehicle, driver, planned departure = first stop's
   planned arrival, origin/destination from first pickup and last drop).
2. Point every task's `Order` at that trip, set `vehicle`, `driver`, `vendor`, status
   `assigned`, and write `Order.dispatched_at` when the route starts.
3. Write `Waypoint` rows from `PlannedStop`, sequence preserved, `planned_arrival` set — the
   first time anything in this system computes a stop order rather than accepting a typed one.
4. `set_vehicle_status(vehicle, "allocated", trip=trip, reason=f"Dispatch plan {plan.code}")`.
5. For hired routes, create or link the `VehicleHire` and send the vendor confirmation.
6. Log a `TrackingActivity` per order and a `PlanEvent` for the commit.
7. Convert any planned `Indent` into an `Order` first, through the existing
   `IndentViewSet.convert` path, so there is one conversion implementation, not two.

**Commit is the only mutating step.** Solving is free and side-effect-free: a dispatcher can run
ten scenarios and throw nine away. That property is worth protecting in code review.

### 10.1 Partial commit

A plan may be committed route by route (`POST .../routes/{id}/commit/`) so the morning's certain
work goes out while the afternoon is still being negotiated with vendors. Committed routes lock;
re-solving the plan leaves them untouched.

### 10.2 Manual override

The dispatcher is the authority, always. `POST .../routes/{id}/move-stop/` moves a task between
routes or reorders stops, and the module immediately re-costs and re-validates that route,
returning any violation (capacity exceeded, window missed, temperature clash) as a warning —
not a refusal. An override that breaks a hard constraint is recorded in `PlanEvent` with the
operator's name. Software that refuses a dispatcher who can see the yard is software that gets
worked around.

---

## 11. API surface

Under `/api/v1/`, following the existing `FilterableViewSet` conventions
(`backend/fleet/views.py:255`):

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/dispatch-plans/` | List plans, filter by branch, date, status |
| `POST` | `/dispatch-plans/` | Create a run: branch, plan_date, horizon, objective weights |
| `GET` | `/dispatch-plans/readiness/` | Pre-flight: unmapped places, missing capacities, stale fixes, expired documents (§14) |
| `POST` | `/dispatch-plans/{id}/collect/` | Pull indents and orders into `DispatchTask` rows |
| `POST` | `/dispatch-plans/{id}/solve/` | Solve. Sync under the §13 threshold, else queued |
| `GET` | `/dispatch-plans/{id}/` | Plan with routes, stops, dropped tasks, KPIs |
| `GET` | `/dispatch-plans/{id}/explain/?task=` | Why this task landed here: chosen vehicle, runners-up, cost delta, binding constraint |
| `POST` | `/dispatch-plans/{id}/routes/{rid}/move-stop/` | Manual override with re-cost |
| `POST` | `/dispatch-plans/{id}/routes/{rid}/lock/` | Freeze a route against re-solve |
| `GET` | `/dispatch-plans/{id}/hire-requirements/` | What the plan wants to buy |
| `POST` | `/dispatch-plans/{id}/request-quotes/` | RFQ fan-out through the outbox |
| `POST` | `/carrier-offers/{id}/accept/` | Award, create `VehicleHire`, optionally re-solve |
| `POST` | `/dispatch-plans/{id}/commit/` | Commit whole plan |
| `POST` | `/dispatch-plans/{id}/replan/` | Child plan from live GPS, executed stops locked |
| `GET` | `/dispatch-plans/{id}/kpis/` | Fill rate, dead-km %, on-time %, own-vs-hire, cost/tonne-km |
| `GET` | `/dispatch-plans/{id}/export/` | Trip sheets as PDF via the existing `reportlab` dependency |

`explain` deserves its place in the list. A planner that cannot answer "why not my truck?" does
not survive contact with a dispatch desk. For each task it returns the assigned vehicle, the
next three candidates with their cost delta, and the binding reason for each rejection
("capacity 9,000 kg short", "would miss the 14:00 window by 35 min", "dry body, frozen load").

---

## 12. Console

A new **Planning** entry in the `TRANSPORT` nav group of `app/page.tsx:8`, beside the existing
Dispatch board:

- **Setup rail** — plan date, branch, horizon, vehicle pool toggles (own / attached / hired),
  objective sliders (cost vs service), a readiness banner.
- **Board** — routes as rows, time as the horizontal axis: a Gantt with each stop as a block,
  colour-coded by temperature class, drag-and-drop between rows calling `move-stop`. The
  existing dispatch board already implements drag-and-drop columns (`app/page.tsx:1300`), so the
  interaction vocabulary is established.
- **Map** — routes as polylines over live vehicle positions.
- **Unassigned rail** — dropped tasks with their outsource price and a one-click "raise hire
  requirement".
- **Route drawer** — stop list, load curve, cost breakdown, margin, violations, commit button.
- **Hire panel** — requirements, quotes in, accept.

Permissions: `dispatch.view` to open, `dispatch.plan` to create and solve, `dispatch.commit` to
commit or override — three new codes in `PERMISSION_CATALOGUE` (`backend/iam/models.py:23`),
added to the seeded Dispatcher and Operations manager roles in
`backend/accounting/management/commands/seed_accounting.py`. Branch scoping reuses
`UserProfile.restrict_to_branch`.

---

## 13. Performance budget

| Plan size | Vehicles | Stops | Target | Mode |
|-----------|----------|-------|--------|------|
| Small | ≤ 20 | ≤ 60 | < 3 s | Synchronous request |
| Medium | ≤ 60 | ≤ 250 | < 20 s | Synchronous, extended timeout |
| Large | ≤ 200 | ≤ 1,000 | < 3 min | Queued, polled by the console |
| Very large | > 200 | > 1,000 | — | Decompose by branch and service area, solve in parallel, merge |

Beyond the solver's own time limit, the costs that matter are the matrix (mitigated by the cache
in §6.9 — a warm cache is the difference between 3 s and 3 min) and the input query (one
prefetched pass over vehicles, drivers, documents and cost bases, never per-candidate — §3.3).

There is no Celery in this stack, and the gap analysis records that as a deliberate choice.
Queued solves therefore run as a management command (`python manage.py solve_dispatch_plan
<id>`) driven by cron or invoked in a worker thread, matching the pattern
`voucher_portal` already uses for background PDF generation. If Celery lands for Phase 3/6 of
the gap analysis, the queued path moves onto it with no change to the solver.

---

## 14. Data readiness — the gate

A CVRP is a function of its inputs. `GET /dispatch-plans/readiness/` reports, and blocks a solve
on the first three:

1. **Places without coordinates** — every pickup and dropoff in the horizon must have lat/lng.
   (Today nullable, `backend/fleet/models.py:349`.) Remedy: a geocoding backfill command plus a
   required-on-create rule for places used in planning.
2. **Vehicles without capacity** — `capacity_kg` defaults to 0
   (`backend/fleet/models.py:55`), which the solver reads as "carries nothing". Volume and
   temperature class are new and start empty.
3. **Tasks without weight or window.**
4. Warnings, not blocks: stale GPS fixes, vendors with no rate history, places with unparseable
   `loading_hours`, drivers with no licence expiry recorded.

Shipping the readiness endpoint in Phase 1, before the solver, is what stops the first demo
failing for reasons that have nothing to do with the optimisation.

---

## 15. Testing

Following the `TestCase` conventions in `backend/fleet/tests.py` (959 lines, `BaseFleetOpsTest`
fixtures), new tests in `backend/dispatch/tests/`:

- **Invariants, on every solved plan** — no route exceeds weight or volume; no temperature
  violation; every non-dropped task appears exactly once; pickup precedes its delivery on the
  same vehicle; no time window violated without a recorded soft-cost; committed stops unchanged
  by a re-plan. These are property assertions run over every fixture, so they catch a regression
  in any constraint rather than in the one the test was written for.
- **Golden instances** — a handful of hand-checked scenarios with a known optimum: 4 vehicles /
  12 stops single-class; a mixed dry+frozen day where the naive nearest-vehicle answer is
  provably worse; a day where outsourcing one lane is correct and the plan must choose it; a
  reefer day where the minimum-distance plan is not the minimum-cost plan (§6.6).
- **Determinism** — a fixed seed and time limit give a byte-identical plan, so the golden tests
  are meaningful. Both solvers are covered; CI runs `greedy` by default and `ortools` in an
  opt-in job.
- **Commit correctness** — a six-order route produces one trip, six orders pointing at it,
  waypoints in sequence, vehicle status moved, and — the §3.1 fix — trip freight equal to the
  sum of all six orders.
- **Regression** — solve time on the medium fixture asserted under a ceiling, so a constraint
  added later cannot quietly make planning unusable.

---

## 16. Phases

Each phase is independently shippable and independently useful. Sizing assumes one full-stack
engineer familiar with this codebase; "week" is five working days.

### Phase A — Foundations · ~2 weeks

Vehicle master extension (volume, temperature class, reefer range, compartments, home place,
speed, duty hours). `DispatchTask` / `DispatchPlan` / `PlanVehicle` / `PlannedRoute` /
`PlannedStop` / `PlanEvent` models and migrations. `TravelMatrixEntry` with the haversine
provider and the cache. The readiness endpoint (§14) and a geocoding backfill command. The three
fixes in §3. Seed data extension in `seed_fleetops` giving a realistic mixed dry/reefer fleet.

*Ships:* nothing plans yet, but the fleet is describable and the data is auditable.

### Phase B — Solve and see · ~3 weeks

`greedy.py` (savings + 2-opt), capacity and time dimensions, temperature compatibility, cost
model with the reefer hourly component, `solve` and plan-detail endpoints, the console board and
map, `explain`. Own fleet only.

*Ships:* a dispatcher can plan tomorrow's own-fleet day and see it costed. Read-only — no
commit, so it can run in parallel with the current manual process for as long as it takes to
build trust. That parallel-running period is the point of splitting B from C.

### Phase C — Commit and override · ~2 weeks

Commit (whole plan and per route), manual move with re-cost, locking, trip-sheet PDF export,
`dispatch.*` permissions and role seeding, branch scoping.

*Ships:* the plan replaces the manual allocation for own vehicles.

### Phase D — Third-party hire · ~2 weeks

Outsourcing penalties in the objective, `HireRequirement` and `CarrierOffer`, RFQ through the
outbox, award → `VehicleHire` → re-solve, hired vehicles as first-class `PlanVehicle` rows,
own-vs-hire KPI.

*Ships:* the module now plans the whole operation, not just the part the fleet owns. Depends on
Phase 2 of the gap analysis, which is built.

### Phase E — GPS in the loop · ~3 weeks

Live start positions with freshness flags, projected availability, geofence arrival capture,
plan-vs-actual variance, ETA refresh, re-plan triggers and child plans, learned travel times,
phone check-in for GPS-less hires.

*Depends on Phase 3 of the gap analysis* (device layer and `VehiclePosition` time-series), which
is unbuilt. Until it lands, Phase E works off the denormalised `Vehicle.current_latitude`
/`current_longitude` — usable, less accurate, and flagged as such.

### Phase F — OR-Tools, scale and India constraints · ~3 weeks

`ortools` behind the `DISPATCH_SOLVER` switch, warm starts, breaks and multi-day routes, city
no-entry windows, e-way-bill validity warnings, border dwell, decomposition for the very-large
case, the queued solve command, planner KPI dashboard.

*Ships:* plan quality at 200+ vehicles, and constraints that make the plan correct on Indian
roads rather than merely optimal on a map.

| Phase | Delivers | Weeks | Depends on |
|-------|----------|-------|-----------|
| A | Data model, matrix cache, readiness gate, three defect fixes | 2 | — |
| B | Greedy solver, own fleet, costed plan, board, explain | 3 | A |
| C | Commit, override, permissions, export | 2 | B |
| D | Outsourcing decision, hire RFQ and award | 2 | C, gap-analysis Phase 2 ✅ |
| E | Live positions, projected availability, re-planning, learned times | 3 | B, gap-analysis Phase 3 |
| F | OR-Tools, breaks, India constraints, scale | 3 | B |

Total ~15 weeks to the full module; **5 weeks to a costed plan on screen** (A + B), which is the
milestone worth aiming at first because it is the one that tells you whether the constraint model
matches the yard.

---

## 17. Decisions to confirm

1. **Take the `ortools` dependency?** Recommend yes in Phase F, with `greedy` shipping first and
   remaining the CI default and production fallback.
2. **Which matrix provider?** Recommend self-hosted OSRM: no per-element billing, no rate limit,
   no consignment geography leaving the VPC. Google if live traffic is worth the per-element
   cost on urban distribution.
3. **Dry cargo in a reefer body?** Default allow, config flag to forbid. Operators with food
   contracts usually forbid it.
4. **Open routes or forced return?** Recommend open by default for hired vehicles and
   configurable per own vehicle — forcing every truck home each night is a large and often
   unnecessary cost that the naive formulation imposes by accident.
5. **Auto-commit ever?** Recommend never. Solving is free; committing moves trucks. A dispatcher
   presses the button.
6. **Planning horizon.** Recommend a rolling 48 hours with a hard lock on the next 4, rather than
   a calendar day — Indian dispatch does not stop at midnight.
7. **Does the existing per-order `recommend-vehicles` stay?** Recommend yes: it is the right tool
   for a single urgent load arriving at 15:00, and it should be reimplemented as a one-task plan
   against the same solver so there is one cost model, not two that drift.

---

## 18. What this module is not

It is not a forecasting system: it plans the demand it is given. It is not a load-planning or
3D bin-packing tool — volume is a scalar here, not a pallet arrangement. It is not an
auto-dispatcher: every plan reaches the road through a human. And it is not machine learning —
the only thing it learns is travel time from its own GPS traces, which is measurement, not
prediction. The gap analysis's Phase 7 is where learned cost and duration estimators would
replace the deterministic ones in §6.6, behind the same interface, once there is a year of
history to learn from.
