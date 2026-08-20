# Multi-point delivery freight — attribution, apportionment and recalculation

On a multi-point delivery the freight is **summed, not divided**. Every consignment on a
consolidated run is priced as if it were the only consignment on the truck, and the
consolidated invoice adds those figures together — so the trip charge, the base charge, the
minimum charge and the loading/unloading are each billed once *per drop* instead of once
*per trip*.

This is not a rounding drift. On a ₹30,000 fixed-rate milk run with three drops the customer
is invoiced **₹90,000**, and ₹90,000 of freight income is posted to the ledger.

This plan fixes that, and adds the recalculation path the desk needs once a trip's shape
changes. It is written against `main` at `20e122a`; every "today" claim points at a file and
line, and every number in §2 was produced by running the code, not by reading it.

Read alongside [ONE-TRIP-END-TO-END.md](ONE-TRIP-END-TO-END.md), whose §3.2 fixed exactly this
defect on the **cost** side (`fleet/costing.py`). This document is its mirror image on the
**revenue** side, and reuses that module's apportionment machinery rather than inventing a
second one.

---

## 1. How multi-point delivery is modelled today

The FMS represents a multi-point run in two different ways, and both are in live use:

| Shape | Model | Billing entity |
|---|---|---|
| **A. Consolidated trip** — several consignments, one truck, one route | N `Order` rows with the same `Order.trip` (`fleet/models.py:592`) | One `Order` per drop; `build_invoice_from_trip` consolidates |
| **B. Multi-drop order** — one consignment, one consignee chain, several stops | One `Order` with N `Waypoint` rows (`fleet/models.py:704`) | The single `Order` |

Shape A is what the dispatch planner commits (`dispatch/views.py:394-427`) and what
`POST /trips/{id}/add-order/` (`fleet/views.py:285`) builds by hand. Shape B is what
`Waypoint.sequence` exists for.

Freight is computed in exactly one place for both:

```python
# fleet/models.py:633
def price_from_rate_card(self, save=True):
    if not self.service_rate:
        return None
    breakdown = self.service_rate.quote(distance_km=self.distance_km, weight_kg=self.weight_kg,
                                        other_charges=self.other_charges)
    self.freight_amount = money(breakdown["freight"] + breakdown["fuel_surcharge"] + breakdown["handling_charges"])
```

`quote()` (`fleet/models.py:523`) knows nothing about the trip the order rides on, and
`Waypoint` is never consulted. That single fact is the root of everything below.

---

## 2. The defect, measured

Reproduced by scripting the real models against a test database. Every figure below is
output, not estimate.

### 2.1 A fixed per-trip rate card is charged once per drop

`RATE_TYPES` includes `per_trip` — "Fixed per trip" (`fleet/models.py:349`). One rate card at
₹30,000 for the run, one truck, three drops:

```
ORD-MP-0: freight=30000.00  tax=1500.00  total=31500.00
ORD-MP-1: freight=30000.00  tax=1500.00  total=31500.00
ORD-MP-2: freight=30000.00  tax=1500.00  total=31500.00

rate card says the trip is worth : 30000.00
sum of order freight             : 90000.00      <- 3x
consolidated invoice INV-...     : freight=90000.00  tax=4500.00  total=94500.00
```

The customer is billed three times the contracted trip rate. `build_invoice_from_trip`
(`fleet/billing.py:133`) sums `o.freight_amount` across the orders, and
`post_customer_invoice` (`accounting/services.py:81`) credits all ₹90,000 to Freight income —
so the error reaches the books, the GST return and the receivable.

### 2.2 Base, minimum and handling charges are replicated per drop

A per-km card with a ₹5,000 base, an ₹8,000 minimum and ₹1,500 + ₹1,500 loading/unloading,
three drops of 60 km each on one 180 km route:

```
ORD-MQ-0: freight=11000.00
ORD-MQ-1: freight=11000.00
ORD-MQ-2: freight=11000.00
sum                            : 33000.00
one base, one loading, 3 drops : 50 x 180 + 5000 + 1500 + (3 x 1500) = 20000.00   <- 1.65x
```

The per-km component is legitimately per-consignment. The ₹5,000 base and the ₹1,500 loading
are not — one truck was loaded once and made one journey, and both were charged three times.
`minimum_charge` is worse: it floors *each drop* at the whole trip's minimum.

Note that the corrected figure is ₹20,000 rather than ₹17,000: unloading *is* per-drop, so
three drops are unloaded three times. The fix is not "charge less", it is "charge each
component on the thing it actually attaches to" — see §3.1.

### 2.3 A multi-drop order's waypoints are invisible to pricing

Shape B, one order, three `Waypoint` rows:

```
ORD-WP-1: 3 waypoints, freight=17000.00
waypoint count seen by pricing: 3  ->  priced anyway as one pickup->dropoff leg
```

The intermediate stops cost the fleet real time and real diesel and are billable under most
rate contracts, but no unloading charge, no per-drop charge and no leg distance reaches the
quote. The opposite error to §2.1 — here multi-point delivery is *under*-billed, and no stop
can be attributed, evidenced or disputed individually.

### 2.4 Trip freight and order freight are never reconciled

`Trip.freight_amount` (`fleet/models.py:255`) is the trip sheet's typed figure, used "when this
trip has no linked order to price it from". `Trip.settlement_summary()` (`fleet/models.py:271`)
chooses between the two:

```python
orders_total = self.orders.aggregate(value=models.Sum("total_amount"))["value"]
freight = money(orders_total) if orders_total else money(self.freight_amount)
```

```
orders unpriced (sum 0)      -> settlement freight 30000.0  (falls back to the trip)
orders priced 15000 each     -> settlement freight 30000.0  (trip.freight_amount 30000.00
                                silently ignored, no reconciliation, no warning)
```

The `or` is doing load-bearing work with no audit trail. Two operators can enter two
different truths and the system will pick one without telling anybody — and if the fallback
ever changed to a sum, that is a third duplication on top of §2.1.

### 2.5 GST is counted as freight revenue

`settlement_summary` sums `total_amount`, which is freight **plus tax**:

```
order total_amount 31500.00 = freight 30000.00 + GST 1500.00
trip freight reported: 94500.00      <- GST counted as revenue
```

`trip_profit`, `per_km_rev` and the trip-profitability report (`fleet/views.py:2219`) all
inherit it. `project_lane` (`fleet/billing.py:214`) deliberately excludes GST from revenue —
"it is collected on behalf of the government, not earned" — so the pre-trip projection and the
post-trip settlement disagree by the GST rate on every single trip.

### 2.6 Adjacent, named but out of scope

- `build_invoice_from_trip` takes `gst_percent` and `reverse_charge` from `billable[0]`
  (`fleet/billing.py:136`). Orders on one trip with different rate cards silently inherit the
  first card's GST treatment.
- `place_of_supply` is taken from `billable[0].dropoff.state`. A milk run crossing state lines
  has one place of supply on the invoice, which is the CGST/SGST-vs-IGST decision.

Both are real, both are GST-correctness rather than freight-arithmetic, and both should be a
separate change. They are listed here so the next reader does not think they were missed.

---

## 3. The rule this plan implements

Stated once, plainly, because everything below is a consequence of it:

> **A charge that belongs to the journey is levied once per trip and divided among the
> consignments on it. A charge that belongs to a consignment is levied on that consignment.
> An order with its own rate card is priced from its own rate card. No figure is ever counted
> twice.**

### 3.1 Component scope

Every `ServiceRate` component is classified. This is intrinsic to what the component *means*,
not a per-card preference:

| Component | Scope | Why |
|---|---|---|
| `per_km_rate`, `per_ton_km_rate` | **Per consignment** | Scales with the consignment's own leg and weight |
| `per_kg_rate` | **Per consignment** | Scales with the consignment's own weight |
| `unloading_charge` | **Per consignment** | One drop per order, and each drop is unloaded |
| `base_charge` | **Per trip** | A charge for putting a truck on the road |
| `minimum_charge` | **Per trip** | A floor on the whole run, not on each drop |
| `per_trip` rate type | **Per trip** | It says so in the name |
| `per_hour_rate` | **Per trip** | Detention and rental are measured on the vehicle |
| `halting_charge_per_day` | **Per trip** | The truck halts, not the consignment |
| `loading_charge` | **Per pickup** | Once per distinct pickup place on the route |
| `fuel_surcharge_percent` | **Follows its base** | A percentage; applies to whichever part it is computed on |
| `other_charges` | **Per consignment** | Already keyed to the order |

The one genuinely ambiguous case is `base_charge` on a PTL/parcel card, where it can mean a
per-consignment booking fee. Handled by one new field rather than by guessing — see §4.1.

### 3.2 Which distance the per-km component uses

Flagged explicitly because it is a commercial decision, not an arithmetic one, and leaving it
implicit is how §2.2 happened.

`Order.distance_km` is the straight line from the pickup to *that order's own* drop
(`fleet/views.py:947`). On a chained run — Bhiwandi → Pune → Satara → Kolhapur — the three
orders carry 60, 120 and 180 km while the truck runs 180. Summing them bills 360 km of a
180 km journey.

Two defensible readings:

- **Haul distance** (sum 360 km) — each consignment pays for how far *its* cargo travelled.
  Correct when the drops are different customers who each bought a lane.
- **Leg distance** (sum 180 km) — each consignment pays for the incremental road its drop
  added. Correct when one customer bought one run, which is the §2 case.

**Recommendation: leg distance, computed from the stop sequence**, because a consolidated run
is one journey and the customer is buying the journey. It also makes the per-km component
consistent with `apportion_trip_cost`, which already splits diesel on a distance basis — so
revenue-per-km and cost-per-km are finally measured against the same kilometres.

`ServiceRate.fixed_charge_scope` (§4.1) carries this too: a card set to `per_consignment` keeps
haul distance, since a per-consignment card is by definition pricing consignments separately.

### 3.3 The invariant

For any trip `T`:

```
sum(o.freight_amount for o in T.orders)  ==  trip_level_freight  +  sum(consignment_level_freight)
```

and `T.freight_amount` is **never** added to that sum — it is a fallback for a trip carrying
no orders, and nothing else.

This is enforced by `reconcile_trip_freight(trip)` (§4.3), asserted by test, and surfaced as a
cockpit blocker rather than left as a convention.

---

## 4. Design

### 4.1 Schema

Five additions. No field is removed and no existing field changes meaning, so every current
reader keeps working.

```python
# ServiceRate — the one ambiguity from §3.1, made explicit instead of guessed
fixed_charge_scope = CharField(choices=[("per_trip", "Once per trip"),
                                        ("per_consignment", "Once per consignment")],
                               default="per_trip",
                               help_text="How base, minimum, halting and per-hour charges are "
                                         "levied on a consolidated multi-point run")

# Order — provenance, so a reader can always see where a number came from
freight_source = CharField(choices=[("rate_card", "Priced from its own rate card"),
                                    ("trip_share", "Share of a trip-level freight"),
                                    ("mixed", "Own rate card plus a trip share"),
                                    ("manual", "Entered by hand")],
                           default="manual")
freight_basis = JSONField(default=dict, blank=True)   # the snapshot behind freight_amount

# Trip — the audit trail for the split
freight_basis = JSONField(default=dict, blank=True)

# Waypoint — per-stop attribution for shape B (§2.3)
freight_share = DecimalField(max_digits=12, decimal_places=2, default=0)
leg_distance_km = DecimalField(max_digits=10, decimal_places=2, default=0)
```

`Order.freight_amount` stays the single authoritative money field and stays equal to
`own components + apportioned share`, so `build_invoice_from_order`, `build_invoice_from_trip`,
`settlement_summary`, the cockpit and every report keep reading the field they read today and
cannot double-count by construction.

**`fixed_charge_scope` defaults to `per_trip`** because that is the correct semantics for a
lorry rate card and because the current behaviour is the bug. The migration must therefore
print a report of every existing `ServiceRate` whose orders would reprice, so the desk can flip
the genuine per-consignment cards deliberately. Existing stored `Order.freight_amount` values
are **not** rewritten by the migration — repricing is an explicit act (§4.4), never a silent
side effect of a deploy.

### 4.2 `fleet/freight.py` — the new module

Mirrors `fleet/costing.py` deliberately, down to the docstring shape, and **imports its
`_split_by_share`** so there is exactly one implementation of the last-order-takes-the-
remainder rounding invariant.

```python
def quote_scoped(rate, *, scope, distance_km=0, weight_kg=0, hours=0,
                 halt_days=0, other_charges=0, pickups=1):
    """`ServiceRate.quote` split by §3.1 scope. scope='trip' returns only the
    journey-level components; scope='consignment' only the per-consignment ones.
    quote_scoped(trip) + quote_scoped(consignment) == quote() for a single-order
    trip, exactly - which is the backward-compatibility guarantee."""

def apportion_trip_freight(trip, *, basis=None):
    """Split `trip`'s journey-level freight across the orders riding on it.

    Basis, in order of preference, reported explicitly so a reader knows whether
    a number was measured or assumed - the same ladder `apportion_trip_cost` uses:

      1. distance - an order's leg share of the trip's total
      2. weight   - weight_kg share, when no order has a distance
      3. equal    - evenly, when neither is known

    An order carrying its own rate card is priced from it and takes no share of a
    trip-level basis it is not party to; an order without one takes its share.

    Returns {"basis": str, "trip_freight": Decimal, "orders": {order_id: {
        "own_freight": Decimal, "trip_share": Decimal, "freight": Decimal,
        "source": str, "components": {...}}}, "balanced": bool}
    """

def reconcile_trip_freight(trip):
    """The §3.3 invariant as a callable: {"balanced": bool, "expected": Decimal,
    "actual": Decimal, "difference": Decimal, "reason": str}."""
```

Worked example, §2.1's trip, `basis="distance"` with equal legs:

| Order | Own rate card | Own freight | Trip share | Freight | Source |
|---|---|---|---|---|---|
| ORD-MP-0 | (shared card, per_trip) | 0.00 | 10,000.00 | 10,000.00 | `trip_share` |
| ORD-MP-1 | (shared card, per_trip) | 0.00 | 10,000.00 | 10,000.00 | `trip_share` |
| ORD-MP-2 | (shared card, per_trip) | 0.00 | 10,000.00 | 10,000.00 | `trip_share` |
| | | | **30,000.00** | **30,000.00** | ✔ balanced |

And §2.2's, where the per-km part is the consignment's own and the base/handling are shared:

| Order | Own freight (60 km × ₹50 + ₹1,500 unload) | Trip share (₹5,000 base + ₹1,500 load) | Freight |
|---|---|---|---|
| ORD-MQ-0 | 4,500.00 | 2,166.67 | 6,666.67 |
| ORD-MQ-1 | 4,500.00 | 2,166.67 | 6,666.67 |
| ORD-MQ-2 | 4,500.00 | 2,166.66 | 6,666.66 |
| | 13,500.00 | 6,500.00 | **20,000.00** |

The remainder paisa lands on the last order, so the parts sum to the whole exactly. The
₹8,000 minimum is applied to the ₹20,000 trip total, once, and does not bind.

### 4.3 Where it wires in

| Call site | Change |
|---|---|
| `Order.price_from_rate_card` (`models.py:633`) | Delegates to `fleet.freight` when the order is on a multi-order trip; unchanged behaviour for a solo order |
| `build_invoice_from_trip` (`billing.py:133`) | Prices via `apportion_trip_freight` before summing, so the sum is of shares, not of replicas |
| `build_invoice_from_order` (`billing.py:69`) | Same, so single-order billing of a consolidated trip's order cannot disagree with the consolidated path |
| `Trip.settlement_summary` (`models.py:264`) | Sums `freight_amount` (taxable), **not** `total_amount` — fixes §2.5; reports `tax` separately; reports the reconciliation |
| `trip.cockpit` (`views.py:440`) | New per-order `freight_source` and a blocker when `reconcile_trip_freight` is unbalanced |
| `report_trip_profitability` (`views.py:2219`) | Revenue from `freight_amount`, matching `project_lane` |

`apportion_trip_cost` is untouched. The two modules stay symmetric: one splits what the trip
cost, the other splits what the trip earned.

### 4.4 Recalculation

The second half of the request. Today `POST /orders/{id}/reprice/` (`views.py:1202`) exists but
is per-order, and on a consolidated trip re-applying it is exactly what re-creates §2.1.

**New: `POST /api/v1/trips/{id}/recalculate-freight/`**

```jsonc
// request
{ "preview": true,          // dry run: compute and return, write nothing
  "basis": "distance",      // or "weight" | "equal" | null to auto-select
  "force": false }          // required to reprice an order that is already invoiced

// response
{ "trip": "TRP-260820AB12",
  "basis": "distance",
  "trip_freight": 30000.00,
  "orders": [
    { "id": 41, "number": "ORD-MP-0", "before": 30000.00, "after": 10000.00,
      "delta": -20000.00, "source": "trip_share", "components": { ... } }
  ],
  "total_before": 90000.00, "total_after": 30000.00, "delta": -60000.00,
  "reconciliation": { "balanced": true, "difference": 0.00 },
  "blockers": [] }
```

Rules:

- **Preview by default in the UI.** Nothing is written until the desk sees the before/after
  table. Money changing under an operator without being shown to them first is how the current
  defect stayed invisible.
- **Idempotent.** Recalculating twice gives the same numbers.
- **Invoice guard.** An order already carrying a raised invoice is a blocker, not a silent
  overwrite. `force: true` reprices the order and flags the invoice as needing a credit/debit
  note; raising that note is *not* in scope and the response says so.
- **Atomic.** One `transaction.atomic` across the whole trip, so a trip is never left half
  repriced and the §3.3 invariant can never be observed broken.
- **Audited.** One `TrackingActivity` per order (`FREIGHT_RECALCULATED`, old → new → basis) and
  the resulting split stored on `Trip.freight_basis`.

**Changed: `POST /orders/{id}/reprice/`** routes through the trip-aware path when the order is
on a multi-order trip, so repricing one drop cannot desynchronise its siblings, and returns
`freight_source` alongside the breakdown.

**Also recalculated on shape change** — adding or removing an order changes every other
order's share, so `add-order` (`views.py:285`) and `remove-order` (`views.py:307`) re-apportion
in the same transaction. Both already refuse to run on anything but a `planned` trip, so no
invoiced freight can move underneath a bill.

### 4.5 Shape B — per-stop attribution

Smaller, and separable from the billing fix.

- `Waypoint.leg_distance_km` is computed from the stop chain (`haversine_km` between
  consecutive places, `fleet/models.py:13`), so a 3-stop order stops being priced as one leg.
- `quote_scoped(scope="consignment")` charges `unloading_charge` per drop waypoint rather than
  once, and the per-km component against the summed leg distance.
- `Waypoint.freight_share` stores each stop's attributed slice via the same
  `_split_by_share`, so the shares sum to `Order.freight_amount` exactly and a customer
  querying one drop can be answered.

### 4.6 Console

- **Trip cockpit** — a *Recalculate freight* button opening the §4.4 preview table (order, old,
  new, delta, source) with an explicit Apply. The unbalanced-reconciliation blocker renders
  beside the existing ones (`app/page.tsx:1503`).
- **Order drawer** — the existing *Reprice* button (`app/page.tsx:2436`) gains a source badge:
  "From rate card" or "Share of trip freight — 1 of 3 drops", and warns before repricing an
  order that rides with others.
- **Trip settlement drawer** — the Freight field (`app/page.tsx:1736`) is labelled as the
  no-orders fallback it is, and shows the order-derived figure when one exists instead of
  letting two truths sit in one form.

---

## 5. Phasing

| Phase | Scope | Ships |
|---|---|---|
| **1** | `fleet/freight.py`, `quote_scoped`, `apportion_trip_freight`, `reconcile_trip_freight`, `fixed_charge_scope`, wire into `build_invoice_from_trip` and `build_invoice_from_order` | The over-billing stops. §2.1, §2.2 |
| **2** | `Trip.settlement_summary` on `freight_amount`, trip-profitability revenue, reconciliation blocker in the cockpit | Revenue stops including GST and stops disagreeing with `project_lane`. §2.4, §2.5 |
| **3** | `recalculate-freight` endpoint, trip-aware `reprice`, re-apportion on add/remove-order, audit trail | The recalculation option. §4.4 |
| **4** | Waypoint leg distance and per-stop shares | Shape B is priced and attributable. §2.3, §4.5 |
| **5** | Console: preview table, source badges, settlement-drawer labelling | The desk can see and drive it. §4.6 |

Phases 1 and 2 are the correctness fix and should land together. Phase 3 is the feature. Phases
4 and 5 are independent of each other.

---

## 6. Tests

Regression tests reproducing §2 exactly, so these numbers can never come back:

1. `test_a_per_trip_rate_card_is_charged_once_across_three_drops` — the §2.1 trip invoices at
   ₹30,000, not ₹90,000.
2. `test_base_and_handling_charges_are_not_replicated_per_drop` — §2.2 sums to ₹20,000.
3. `test_apportioned_shares_sum_to_the_trip_freight_exactly` — including the odd-paisa case,
   asserting the last-order remainder.
4. `test_an_order_with_its_own_rate_card_is_priced_from_it_not_apportioned` — the "attributed
   from rate card if mapped" rule.
5. `test_a_single_order_trip_prices_identically_to_today` — the backward-compatibility
   guarantee for the overwhelmingly common case.
6. `test_settlement_freight_excludes_gst` — §2.5.
7. `test_trip_freight_amount_is_never_added_to_order_freight` — the §3.3 invariant.
8. `test_recalculate_is_idempotent` and `test_recalculate_preview_writes_nothing` — the latter
   mirroring the existing `test_listing_does_not_rewrite_the_stored_freight`
   (`fleet/tests.py:2152`).
9. `test_recalculate_refuses_an_invoiced_order_without_force`.
10. `test_adding_an_order_to_a_trip_re_apportions_every_share`.
11. `test_a_multi_drop_order_prices_each_stop` — §4.5, shares summing to the order's freight.

---

## 7. Risk

- **Numbers move.** Any customer on a `per_trip` or high-`base_charge` card has been
  over-invoiced on consolidated runs. Correcting forward is straightforward; already-raised
  invoices are not touched by this change and need a commercial decision plus credit notes,
  which this plan does not make. The migration report (§4.1) is what makes the exposure
  visible before anything is repriced.
- **`fixed_charge_scope` default.** `per_trip` is right for lorry freight and wrong for a
  parcel booking fee. The migration report lists the cards to review; the field exists so the
  answer is recorded rather than assumed.
- **Leg distance is straight-line.** `haversine_km` is the fleet's existing distance
  primitive and is consistent with how `Order.distance_km` is already derived
  (`fleet/views.py:947`), so the apportionment basis is at least self-consistent. A road-
  distance matrix is a separate upgrade and would improve this without changing its shape.
