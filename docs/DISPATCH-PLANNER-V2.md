# Dispatch planner v2 — implementation plan

The planner shipped in `backend/dispatch/` has the right bones — a clean data model, a
working greedy CVRP, a commit path that lands real trips — but as a **product** it is thin:
the dispatcher cannot tell it *how* to plan, cannot *see* the plan it produced, and half the
constraint machinery inside the solver is unreachable dead code.

This plan closes that. It is written against the code as it stands on
`claude/cvrp-dispatch-planning-ewm262`; every "today" claim points at a file and line.

Read alongside [DISPATCH-PLANNING.md](DISPATCH-PLANNING.md), which remains the reference for
the original design. This document supersedes its §10.2, §11 and §12.

---

## 1. What is actually wrong today

Not a wishlist — these are defects and dead code found by reading the module end to end.

### 1.1 There are no strategies. At all.

`DispatchPlan.objective` is a `JSONField` documented as *"Cost/service weight overrides for
the solver"* (`backend/dispatch/models.py:39`). **Nothing ever reads it.** A repo-wide grep
for `objective` returns the model field, the migration, and one comment.

`DispatchPlanViewSet.solve` (`backend/dispatch/views.py:90`) ignores `request.data`
completely. `solve_plan(plan)` takes no parameters beyond the plan. The greedy cost function
(`backend/dispatch/solver/greedy.py:151`) is a hardcoded expression:

```python
cost = added_km * pv.cost_per_km + hours * pv.cost_per_hour + len(violations) * WINDOW_MISS_PENALTY
```

with `WINDOW_MISS_PENALTY = Decimal("500")` as a module constant (`greedy.py:26`). The
own-vs-outsource decision (`greedy.py:196`) is a bare `<=` comparison with no bias term.

**Consequence:** every plan is solved exactly one way. A dispatcher who wants "keep my own
trucks full even if it costs a bit more" or "get everything there on time, spend what it
takes" has no way to say so. This is the single biggest gap and the headline of this plan.

### 1.2 The entire temperature/reefer subsystem is unreachable

`inputs.collect_tasks` hardcodes `temperature_class="dry"` on every task it creates
(`backend/dispatch/solver/inputs.py:118`). It cannot do otherwise: **`fleet.Order` has no
`temperature_class` field** (`backend/fleet/models.py:511-546` — `Vehicle` has one,
`Order` does not).

So `temperature_compatible()` (`greedy.py:29`), the `PRECOOL_MINUTES = 45` pre-cool logic
(`greedy.py:117`), `Cluster.needs_cooling` (`greedy.py:65`), the reefer `cost_per_hour`
basis (`costing.py:41`) and the matching OR-Tools constraints are all **dead code that can
never fire**. A frozen consignment will be planned onto a dry truck, silently.

### 1.3 Demand collection is lossy

`collect_tasks` (`inputs.py:105-121`) also hardcodes:

| Field | Hardcoded to | What is lost |
|---|---|---|
| `task_type` | `"ftl"` | Multi-drop legs are never typed as such |
| `priority` | `"normal"` | `must_go` / `deferrable` unreachable — so `Cluster.must_go` (`greedy.py:64`) never fires either |
| `pickup_window_start/end` | `null` | Plant loading hours ignored entirely |
| `drop_window_start` | `null` | Only `drop_window_end` is set, from `required_at`/`scheduled_at` |

Window feasibility is therefore half-blind: the solver can tell you a delivery is late, but
never that a pickup is too early for the plant to load it.

`collect_tasks` also has no filters — it sweeps up *every* open indent and un-trip'd order
for the branch. There is no way to plan one lane, one customer, or one shift.

### 1.4 KPIs are thin, and one is silently always zero

`PlannedRoute.dead_km` (`models.py:159`) is **never assigned**. `engine.solve_plan` creates
the route without it (`engine.py:48-52`) and never comes back. Every route reports 0 dead km.
(`fleet/allocation.py:43` has an unrelated `_dead_km` for single-order scoring — the planner
does not use it.)

`plan.summary` (`engine.py:114-120`) carries 10 numbers. Missing, in rough order of how often
a dispatcher would ask for them: cost per tonne-km, average weight/volume utilisation,
own-vs-hire split by value, projected on-time %, dead-km ratio, stops per route, average route
duration, revenue per km.

`utilisation_volume_percent` is only computed when `capacity_cbm` is set (`engine.py:78`) —
and `Vehicle.volume_cbm` is a recent field, so it is 0 on most rows, making volume
utilisation silently always zero too.

### 1.5 There is no plan view worth the name

`DispatchPlanningView` (`app/page.tsx:1844`) is a table of plans and a drawer. Routes render
as **one line of text spans** (`app/page.tsx:1955-1958`):

```tsx
{(route.stops || []).map((stop: any) => <span key={stop.id} …>
  {stop.stop_type === "pickup" ? "▲" : "▼"} {stop.place_name} ({stop.load_after_kg}kg)</span>)}
```

No map. No timeline. No load graph. No per-stop ETA display. No way to see which orders are
on which truck other than reading a run-on sentence of place names.

A production-quality Leaflet map **already exists** in this codebase — `FleetMap`
(`app/page.tsx:2489`) with marker clustering, a basemap layer switcher and India bounds. The
planner does not use it.

It cannot, yet, for a mechanical reason: **`PlannedStopSerializer` (`serializers.py:22`)
exposes `place_name` but no coordinates.** You cannot draw a route line from names.

### 1.6 Third-party sourcing is one national number

`costing.spot_rate_per_km()` (`costing.py:69`) is `own_cost_per_km × 1.15` — a **single rate
for the entire country**, every lane, every vehicle type, every season. Every task's
`outsource_estimate` is `distance × that one number` (`inputs.py:113`).

`VehicleHire` history is only consulted for vendors that already have an *attached vehicle on
file* (`costing.vendor_cost_basis`, keyed on `vehicle.vendor_id`). Actual historical lane
rates — the most valuable pricing signal the business owns — are never used to price spot.

### 1.7 No manual override, no what-if

`PlannedStopViewSet` is read-only apart from `arrive`/`depart` (`views.py:305-324`). There is
no endpoint to move a task between routes, reorder stops, or pin a load to a truck and
re-cost. DISPATCH-PLANNING.md §10.2 promises this; it does not exist.

Nor can two strategies be compared — there is nothing to compare, per §1.1.

### 1.8 Drivers are never planned

`build_plan_vehicles` (`inputs.py:48-89`) never sets `PlanVehicle.driver`. Every route
therefore reaches commit driverless and is **blocked** (`views.py:179`) until a human picks a
driver from a dropdown, one route at a time. Driver licence expiry and duty-hour limits —
promised in §6.8 — are not checked at all.

---

## 2. What v2 delivers

| # | Capability | Phase |
|---|---|---|
| 1 | Named planning **strategies** the dispatcher picks when running the plan | 1 |
| 2 | Tunable weights + hard constraints behind each strategy | 1 |
| 3 | Faithful demand collection: temperature, priority, both windows, task type | 2 |
| 4 | Collection **filters**: lane, customer, date range, vehicle type, temperature | 2 |
| 5 | Extensive KPI set at plan and route level, incl. the ones that are wrong today | 3 |
| 6 | **Route map** — pickups, drops, sequence, per-vehicle colour, live start position | 4 |
| 7 | Route timeline (Gantt) and load-utilisation bars | 4 |
| 8 | Per-route detail: stops, ETAs, orders carried, load after each stop | 4 |
| 9 | Lane-level spot pricing from real `VehicleHire` history | 5 |
| 10 | Vendor capability matching and per-vendor lane rates | 5 |
| 11 | Manual override: move a task between routes, re-cost immediately | 6 |
| 12 | **Strategy comparison**: solve N ways, compare side by side, adopt one | 6 |
| 13 | Driver auto-assignment with licence and duty checks | 6 |

---

## 3. Phase 1 — Strategies (the headline)

### 3.1 The strategy contract

Replace the free-form `objective` blob with a validated structure. Still stored in the same
`JSONField` (no migration needed on `DispatchPlan`), but shaped and enforced by a serializer.

```python
# backend/dispatch/strategies.py  (new)

STRATEGY_PRESETS = {
    "least_cost":       "Cheapest total plan — outsource freely when the market is cheaper",
    "max_utilisation":  "Keep own trucks as full as possible — outsource only when infeasible",
    "fastest_service":  "Hit every delivery window — spend more to do it",
    "max_margin":       "Maximise revenue minus cost — drop unprofitable deferrable loads",
    "own_fleet_first":  "Exhaust own capacity before buying any market vehicle",
    "balanced":         "Even weighting — the default",
}
```

Each preset expands to a full weight vector, so a dispatcher can start from a preset and
then nudge one number:

```python
DEFAULT_WEIGHTS = {
    "distance_cost":        1.0,   # multiplier on cost_per_km × km
    "time_cost":            1.0,   # multiplier on cost_per_hour × hours
    "fixed_cost":           1.0,   # multiplier on starting a vehicle at all
    "window_miss_penalty":  500.0, # ₹ per missed window  (was greedy.py:26, now tunable)
    "outsource_bias":       1.0,   # ×outsource_estimate before comparing; >1 favours own fleet
    "utilisation_bonus":    0.0,   # ₹ credited per % of weight capacity filled
    "dead_km_penalty":      0.0,   # ₹ per empty km run to reach a pickup
    "own_fleet_discount":   0.0,   # flat ₹ shaved off an own vehicle's route cost
    "margin_weight":        0.0,   # ₹ of revenue counted against cost when choosing
}

CONSTRAINTS = {
    "max_outsource_percent":   None,  # refuse a plan that buys out more than this
    "min_utilisation_percent": None,  # do not start a truck below this fill
    "time_windows":            "soft",# "soft" = penalty | "hard" = infeasible
    "max_stops_per_route":     None,  # overrides PlanVehicle.max_stops when set
    "max_route_km":            None,
    "max_duty_minutes":        None,
    "allow_partial_service":   True,  # False = fail the solve rather than drop a task
}
```

Preset → weights mapping (the part that makes strategies actually behave differently):

| Preset | Key deltas from default |
|---|---|
| `least_cost` | `outsource_bias 1.0`, `fixed_cost 1.0` — pure cost comparison |
| `max_utilisation` | `outsource_bias 3.0`, `utilisation_bonus 40`, `fixed_cost 2.0` — few, full trucks |
| `fastest_service` | `window_miss_penalty 5000`, `time_cost 2.0`, `time_windows "hard"` |
| `max_margin` | `margin_weight 1.0`, drops `deferrable` tasks whose revenue < cost |
| `own_fleet_first` | `outsource_bias 10.0`, `own_fleet_discount 500` |
| `balanced` | defaults as listed |

### 3.2 Threading it through the solver

**`greedy.py`** — the cost function becomes strategy-aware. Today (`greedy.py:151`):

```python
cost = added_km * pv.cost_per_km + hours * pv.cost_per_hour + len(violations) * WINDOW_MISS_PENALTY
```

Becomes, with `strategy` passed down from `solve()`:

```python
w = strategy.weights
cost = (added_km * pv.cost_per_km * w["distance_cost"]
        + hours * pv.cost_per_hour * w["time_cost"]
        + len(violations) * w["window_miss_penalty"]
        + dead_km * w["dead_km_penalty"])
if not route.used:
    cost += pv.fixed_cost * w["fixed_cost"]
if pv.source == "own":
    cost -= w["own_fleet_discount"]
fill = cluster.weight_kg / pv.capacity_kg * 100
cost -= fill * w["utilisation_bonus"]
if w["margin_weight"]:
    cost -= cluster.revenue_estimate * w["margin_weight"]
```

And the outsource decision (`greedy.py:196`) gains the bias term:

```python
threshold = cluster.outsource_estimate * w["outsource_bias"]
if cluster.must_go or not threshold or best_eval["cost"] <= threshold:
    _apply(...)
```

`time_windows: "hard"` turns the window violation from a penalty into a `return None,
"misses the delivery window"` rejection in `_evaluate_cluster`.

**`ortools_solver.py`** — same weights map onto arc costs and the disjunction penalty:
`routing.AddDisjunction([node], penalty)` takes `outsource_estimate × outsource_bias ×
MONEY_SCALE`; `own_fleet_discount` becomes a negative fixed cost per vehicle;
`time_windows: "hard"` switches the Time dimension's slack to 0 at each node.

**`engine.solve_plan(plan, strategy=None)`** resolves the strategy once (from the argument,
falling back to `plan.objective`, falling back to `balanced`), persists the resolved vector
back onto `plan.objective` so the plan stays reproducible, and passes it to whichever solver
runs.

### 3.3 API

```
POST /api/v1/dispatch/plans/{id}/solve/
{
  "strategy": "max_utilisation",              # preset name, or…
  "weights":  { "outsource_bias": 2.5 },      # …partial overrides merged onto the preset
  "constraints": { "max_outsource_percent": 30, "time_windows": "hard" }
}
```

`GET /api/v1/dispatch/strategies/` returns the preset catalogue with descriptions and the
weight vector each expands to, so the UI builds its picker from the server rather than
hardcoding a list.

Post-solve, `max_outsource_percent` / `min_utilisation_percent` are checked against the
result and reported as `plan.summary.constraint_breaches` — the plan still solves and is
shown, but the breach is surfaced rather than silently accepted.

### 3.4 UI

A **Run plan** panel replacing today's bare "Solve" button (`app/page.tsx:1933`):

- Strategy cards (radio) — name, one-line description, and the two or three weights that
  define it, so the choice is legible rather than a magic word
- An "Advanced" disclosure with sliders for each weight and inputs for each constraint
- The chosen strategy is shown on the plan header and in the plans table, so a plan is never
  a mystery after the fact

### 3.5 Files

| File | Change |
|---|---|
| `backend/dispatch/strategies.py` | **new** — presets, weight resolution, validation |
| `backend/dispatch/serializers.py` | **new** `SolveRequestSerializer` |
| `backend/dispatch/solver/greedy.py` | `solve(…, strategy)`, strategy-aware cost + disjunction |
| `backend/dispatch/solver/ortools_solver.py` | same weights → arc cost, disjunction, time slack |
| `backend/dispatch/solver/engine.py` | `solve_plan(plan, strategy=None)`, persist resolved vector |
| `backend/dispatch/views.py` | `solve` reads the body; `strategies` list endpoint |
| `app/page.tsx` | Run-plan panel with strategy cards + advanced weights |

**Tests:** the same demand solved under `least_cost` vs `own_fleet_first` produces a
measurably different own-vs-outsource split; `max_utilisation` yields fewer routes at higher
fill; `time_windows: "hard"` drops a task that `"soft"` merely penalises; an unknown preset
name is a 400.

---

## 4. Phase 2 — Honest demand collection

### 4.1 Give `Order` a temperature class

One migration on `fleet`:

```python
# fleet/models.py — Order
temperature_class = models.CharField(max_length=10, choices=VEHICLE_TEMPERATURE_CLASSES, default="dry")
temp_set_point_c = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
```

Mirror on `Indent`. This is what un-deadens §1.2's entire subsystem. Default `"dry"` keeps
every existing row valid and every existing test passing.

### 4.2 Collect what the record actually says

`inputs.collect_tasks` stops hardcoding:

```python
temperature_class = obj.temperature_class                       # was "dry"
task_type        = "ftl" if obj.order_type == "ftl" else "multi_drop_leg"
priority         = _priority_from(obj)                          # was "normal"
pickup_window_start, pickup_window_end = _pickup_window(obj)    # was None, None
drop_window_start, drop_window_end     = _drop_window(obj)      # was None, deadline
```

`_priority_from` maps `Order.priority` (the field exists — `fleet/models.py:543`) plus
business rules: an order already past its `scheduled_at` is `must_go`; a `deferrable` flag on
the customer or a far-future window makes it `deferrable`.

`_pickup_window` reads the pickup `Place`'s operating hours. That needs two fields on `Place`
(`opens_at`, `closes_at`, both nullable `TimeField`) — a small migration, and the thing that
finally makes plant loading hours real.

### 4.3 Collection filters

```
POST /api/v1/dispatch/plans/{id}/collect/
{
  "customers":         [12, 18],
  "pickup_places":     [4],
  "temperature_class": "frozen",
  "scheduled_from":    "2026-08-17T00:00:00Z",
  "scheduled_to":      "2026-08-17T23:59:59Z",
  "order_ids":         [301, 302],   # explicit hand-picked set, overrides the rest
  "include_indents":   true
}
```

Stored on the plan as `collection_filters` (new `JSONField`) so a re-collect is repeatable and
the plan records what universe it was planning over.

### 4.4 Files

| File | Change |
|---|---|
| `backend/fleet/models.py` + migration | `Order.temperature_class`, `temp_set_point_c`; same on `Indent`; `Place.opens_at/closes_at` |
| `backend/dispatch/models.py` + migration | `DispatchPlan.collection_filters` |
| `backend/dispatch/solver/inputs.py` | faithful field mapping, filter application |
| `backend/dispatch/views.py` | `collect` reads a filter body |
| `app/page.tsx` | collection filter form on the plan drawer |

**Tests:** a frozen order collected as a frozen task; that task refused by a dry vehicle and
routed to the reefer; a `must_go` task never outsourced even when spot is cheaper; a pickup
window outside plant hours flagged; filters narrowing the collected set.

---

## 5. Phase 3 — KPIs worth showing

### 5.1 Fix what is wrong

- **`dead_km`** — compute it in `_evaluate_cluster` (the leg from the vehicle's current
  position to the cluster pickup is by definition empty) and carry it onto `PlannedRoute`.
  This is a one-line-per-layer fix for a field that has read 0 since day one.
- **`utilisation_volume_percent`** — when `capacity_cbm` is 0, report `null` rather than 0, so
  the UI can say "not tracked" instead of showing a truck as 0% full.

### 5.2 Route-level additions

`laden_km`, `dead_km_percent`, `stop_count`, `orders_carried`, `revenue_per_km`,
`cost_per_tonne_km`, `avg_utilisation_percent` (mean across legs, not just the peak — a truck
that is full for one leg of eight is not a full truck), `projected_on_time_stops`,
`window_risk` (stops arriving within 30 min of their deadline).

### 5.3 Plan-level additions

```python
plan.summary = {
    # … the 10 that exist today, plus:
    "total_dead_km":            …,
    "dead_km_percent":          …,
    "avg_weight_utilisation":   …,
    "avg_volume_utilisation":   …,
    "cost_per_tonne_km":        …,
    "own_fleet_value":          …,   # ₹ of work kept in-house
    "outsourced_value":         …,   # ₹ bought on the market
    "own_vs_hire_percent":      …,
    "projected_on_time_percent":…,
    "stops_per_route":          …,
    "avg_route_duration_hours": …,
    "tasks_by_temperature":     {"dry": …, "chiller": …, "frozen": …},
    "constraint_breaches":      [ … ],   # from §3.3
    "strategy":                 "max_utilisation",
}
```

### 5.4 UI

Replace the flat 10-cell `tracking-grid` (`app/page.tsx:1939`) with a tiered KPI board:
four headline tiles (fill rate, margin, cost/tonne-km, on-time), then a secondary grid, then
a per-route table with sortable columns. Utilisation renders as a bar, not a number.

---

## 6. Phase 4 — The plan view (map, timeline, detail)

### 6.1 Serve the geometry

`PlannedStopSerializer` (`serializers.py:22`) gains:

```python
latitude  = serializers.DecimalField(source="place.latitude",  …, read_only=True)
longitude = serializers.DecimalField(source="place.longitude", …, read_only=True)
city      = serializers.CharField(source="place.city", read_only=True)
order_number = serializers.CharField(source="task.order.number", read_only=True, default="")
customer_name = serializers.CharField(source="task.order.customer.name", read_only=True, default="")
```

`PlannedRouteSerializer` gains a `path` — the ordered `[[lat, lng], …]` including the
vehicle's start position, so the frontend draws a polyline without re-deriving it.

A dedicated `GET /api/v1/dispatch/plans/{id}/map/` returns everything the map needs in one
payload — routes with paths and colours, unrouted tasks, hire requirements, vehicle start
positions — rather than making the client stitch it from the detail serializer.

### 6.2 The map

Reuse `FleetMap`'s proven Leaflet setup (`app/page.tsx:2489`) — dynamic import, the
`window.L` dance markercluster needs, the basemap layer switcher, India bounds — as a new
`PlanMap` component:

- One **colour per route**, consistent between map, timeline and route table
- **Pickup** markers as ▲, **drops** as ▼ numbered by stop sequence
- Polyline per route; dashed for the dead-km leg from vehicle start to first pickup
- **Unrouted / outsourced** tasks as grey markers with a dashed pickup→drop line — the loads
  the plan could not serve are exactly what a dispatcher needs to see
- Click a route in the table → that route highlights, others fade
- Click a marker → popup with order number, customer, weight, ETA, load-after
- Toggles: show/hide outsourced, show/hide dead legs, show/hide vehicle start positions

### 6.3 Timeline and load

- **Gantt** — one row per route, blocks for drive / service / wait, ticks at each stop,
  window markers so lateness is visible as geometry rather than a number
- **Load profile** — a small bar per route showing `load_after_kg` at each stop against
  capacity, which makes an under-filled truck obvious at a glance

### 6.4 Route detail drawer

Selecting a route opens: vehicle and driver, the strategy that produced it, full KPI strip,
an ordered stop table (seq, type, place, ETA, service, wait, load after, order, customer), the
violations list, and lock / assign-driver / commit-this-route actions.

### 6.5 Files

| File | Change |
|---|---|
| `backend/dispatch/serializers.py` | coordinates, order/customer, `path` |
| `backend/dispatch/views.py` | `map` action |
| `app/page.tsx` | `PlanMap`, `RouteGantt`, `LoadProfile`, route detail drawer |
| `app/globals.css` | map, gantt, KPI-tile, load-bar styles |

---

## 7. Phase 5 — Owned vs third-party, properly

### 7.1 Lane-level spot rates

Replace the single national `spot_rate_per_km()` (`costing.py:69`) with a lane lookup that
degrades gracefully:

1. `VehicleHire` history on **this exact lane** (pickup city → drop city), last 90 days, median
2. …else history on the **same corridor** (pickup state → drop state)
3. …else the vendor's own average for **that vehicle type**
4. …else today's national `own_cost × 1.15`

Each returns a `confidence` (`"lane" | "corridor" | "type" | "fallback"`) that surfaces in the
UI, so a dispatcher knows whether a hire estimate is grounded or guessed — the same honesty
`fleet/allocation.py` already applies to vendor rate estimates.

### 7.2 Vendor capability and preference

A `VendorLaneRate` model (vendor, origin, destination, vehicle type, temperature class, rate,
basis, validity window) so negotiated contract rates beat inferred history. RFQ fan-out
(`views.py:346`) narrows to vendors that can actually serve the lane and temperature class,
instead of emailing every active transporter in the database.

### 7.3 Spot capacity as a routable candidate

Today spot capacity is only ever a per-task penalty. Add **virtual `PlanVehicle` rows** —
`source="spot_slot"` (the choice already exists, `models.py:113`) — one per vendor lane rate,
so the solver can genuinely *route* a hired truck through a multi-drop rather than only buying
a point-to-point move. This is what makes `own_fleet_first` and `least_cost` differ in an
interesting way rather than a trivial one.

---

## 8. Phase 6 — Override, compare, drivers

### 8.1 Manual override

```
POST /api/v1/dispatch/plans/{id}/move-task/     { "task": 41, "to_route": 7, "position": 3 }
POST /api/v1/dispatch/plans/{id}/reorder-route/ { "route": 7, "stop_ids": [22, 25, 23] }
POST /api/v1/dispatch/plans/{id}/pin-task/      { "task": 41, "vehicle": 12 }
POST /api/v1/dispatch/plans/{id}/unroute-task/  { "task": 41 }
```

Each re-costs the affected routes immediately and returns the delta, logging a `manual_move`
`PlanEvent` (the type already exists, `models.py:210`) so the audit trail stays complete.
A pinned task survives the next re-solve.

Drag-and-drop in the UI between route cards, with the cost delta shown live.

### 8.2 Strategy comparison

```
POST /api/v1/dispatch/plans/{id}/compare/
{ "strategies": ["least_cost", "max_utilisation", "own_fleet_first"] }
```

Solves each into a **scenario** child plan (`parent_plan` already exists, `models.py:43`;
add `is_scenario`) and returns a comparison table — cost, margin, fill rate, routes used,
own-vs-hire split, on-time %, dead km — per strategy.

`POST /plans/{id}/adopt/ {"scenario": 84}` copies the winning scenario's routes onto the
parent and marks the losers `superseded`. Only the adopted plan can be committed.

This is the feature that turns "the planner produced a plan" into "the dispatcher chose a
plan" — and it is only possible once Phase 1 exists.

### 8.3 Drivers in the plan

`build_plan_vehicles` assigns a driver per vehicle: available status, licence not expired
(`ComplianceDocument` is already queried there for vehicles — extend to drivers), duty hours
remaining, and home-base proximity. Routes then reach commit driverless only when no
legal driver exists, and `views.py:179` blocks for a real reason rather than by default.

---

## 9. Sequencing and effort

| Phase | Scope | Rough size | Depends on |
|---|---|---|---|
| **1 — Strategies** | `strategies.py`, solver threading, solve API, UI picker | ~600 lines, 12 tests | — |
| **2 — Collection** | 2 migrations, faithful mapping, filters | ~350 lines, 10 tests | — |
| **3 — KPIs** | `dead_km` fix, route + plan metrics, KPI board | ~400 lines, 8 tests | 1, 2 |
| **4 — Plan view** | geometry serialisers, `PlanMap`, Gantt, detail drawer | ~900 lines, 6 tests | 3 |
| **5 — Sourcing** | lane rates, `VendorLaneRate`, spot slots | ~500 lines, 10 tests | 1 |
| **6 — Override/compare** | move/pin/reorder, scenarios, drivers | ~700 lines, 12 tests | 1, 3 |

Phases 1 and 2 are independent and are the highest value per line — 1 because it is the
stated ask and unlocks 5 and 6, 2 because it stops the planner quietly lying about frozen
cargo. **Recommended order: 1 → 2 → 3 → 4 → 6 → 5.** Phase 4 lands before 5/6 so the
dispatcher can *see* what the earlier phases changed.

Every phase ships with tests and keeps `USE_SQLITE=true … manage.py test` green; the OR-Tools
path stays optional throughout, exactly as `engine.py:30-36` already arranges.

---

## 10. What this plan does not cover

Stated so the boundary is explicit, not discovered later:

- **Real road distances.** `matrix.py` still falls back to haversine. OSRM/Google providers
  are stubbed in `TRAVEL_PROVIDERS` (`models.py:229`) and remain future work; every distance
  and ETA in v2 is a straight-line estimate with a speed factor.
- **India-specific constraints** — city no-entry hours, e-way bill validity, interstate border
  dwell (DISPATCH-PLANNING.md §9). Unbuilt before, unbuilt after.
- **Multi-day planning.** The horizon stays ≤48h; a route that cannot complete in the horizon
  is infeasible rather than split across days.
- **Automatic re-solve on GPS drift.** `replan` stays dispatcher-triggered (`views.py:134`).
- **A vendor self-service portal.** `CarrierOffer.record_quote` remains dispatch-desk data
  entry for a rate that arrived by phone or email.
