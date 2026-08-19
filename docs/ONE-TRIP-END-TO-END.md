# One trip, end to end — stitching the O2A flow together

Today the pieces of a single trip's life — the order, the truck, the LR, the diesel, the
POD, the bill, the profit — all exist and all work, but they are **operated as eight
separate screens against six separately-keyed tables**. Nobody can open one thing and see
one trip whole, and in one place the numbers are provably wrong because of it.

This plan closes that. It is written against the code as it stands on
`claude/cvrp-dispatch-planning-ewm262` (= `main` at `ca45de3`); every "today" claim points
at a file and line.

Read alongside [O2A-GAP-ANALYSIS.md](O2A-GAP-ANALYSIS.md), which established the
Order↔Trip spine this plan builds on (its Finding 1, resolved in PR #20). This document
supersedes that file's items 10, 15 and 18.

---

## 1. The problem, concretely

Booking one consignment and closing it out today means visiting, in order:

| # | Screen | What the operator does | Re-typed from |
|---|---|---|---|
| 1 | Indents / Orders | Book the consignment | — |
| 2 | Planning / Dispatch | Get it onto a truck | — |
| 3 | **Lorry receipts** | **Re-type consignor, consignee, origin, destination, material, weight** | **the order** |
| 4 | Fuel | Log the diesel | — |
| 5 | Expenses | Log toll, bhatta, loading | — |
| 6 | ePOD | Verify the proof | — |
| 7 | Invoices | Raise the bill | — |
| 8 | Dispatch → trip sheet | Read the trip P&L | — |

Step 3 is pure duplicate data entry, and steps 4–8 each key off a *different* identifier,
which is what makes the P&L wrong (§3.2).

There is no screen anywhere that shows one trip's full stack. The closest are
`GET /trips/{id}/settlement/` (`fleet/views.py:210`) and `GET /orders/{id}/settlement/`
(`fleet/views.py:858`) — two different sheets, neither of which includes the LR, the POD,
or the invoice's ledger posting.

---

## 2. What already exists — do not rebuild it

A large majority of this chain is built and correct. The plan below is mostly *wiring*, not
new subsystems.

| Stage | Status | Where |
|---|---|---|
| Order book | ✅ Solid | `Order` (`fleet/models.py:511`), Indent → Order convert |
| Trip creation | ✅ Solid | `Order.ensure_trip()` (`fleet/models.py:576`), `POST /trips/{id}/add-order/` (`fleet/views.py:162`) |
| **LR generation** | ❌ **Absent** | see §3.1 |
| Trip expenses | 🟡 Captured, ambiguously keyed | `TripExpense` (`fleet/models.py:830`), `FuelEntry` (`fleet/models.py:795`) |
| POD | ✅ Solid, and gates billing | `ProofOfDelivery` (`fleet/models.py:677`), `order_pod_state` (`fleet/billing.py:33`) |
| Freight cost | ✅ Solid | `Order.price_from_rate_card()` (`fleet/models.py:560`), `ServiceRate.quote()` |
| Invoice | ✅ Solid, but order-level only | `build_invoice_from_order` (`fleet/billing.py:41`) → `post_customer_invoice` (`accounting/services.py:76`) |
| Trip P&L | 🟡 Right at trip level, **wrong at order level** | `Trip.settlement_summary()` (`fleet/models.py:208`) vs `order_profitability` (`fleet/views.py:1473`) |

---

## 3. Three structural defects that gate everything else

### 3.1 The lorry receipt is an island

`LorryReceipt` (`fleet/models.py:170`) stores consignor, consignee, origin, destination,
material, weight and packages as **free-text `CharField`s**, duplicating data the `Order`
already holds as proper FKs (`Place`, `Customer`) and typed decimals.

`Order.lorry_receipt` exists as a nullable FK (`fleet/models.py:525`) — and **no application
flow ever populates it.** A repo-wide grep returns the field declaration, the serializer,
and nothing else. It is the exact dangling-FK pattern O2A-GAP-ANALYSIS.md Finding 1
diagnosed for `Order.trip`, still unfixed for the LR.

The knock-on: `dispatch_trip` and `close` (`fleet/views.py:196`, `:205`) both call
`trip.lorry_receipts.update(status=...)` to advance the LR through the trip lifecycle. Since
that M2M is never populated from an order, **both statements are no-ops in production.** The
LR status field never moves on its own.

There is also no LR document generator and no LR PDF, though `reportlab` is already a
dependency and is used for invoice and voucher PDFs.

### 3.2 Cost is keyed to the trip, revenue to the order, and nothing apportions

This is the defect that makes a number wrong rather than merely inconvenient.

A trip can carry many orders — that is the whole point of `add-order` and of the milk-run
support in the dispatch planner. `Trip.settlement_summary()` handles this correctly on the
revenue side, and says so (`fleet/models.py:213`):

```python
# A trip carries every order allocated to it (a consolidated multi-drop route
# is several orders on one trip), so its freight is their sum
orders_total = self.orders.aggregate(value=models.Sum("total_amount"))["value"]
```

But `order_profitability` (`fleet/views.py:1478`) does this:

```python
expenses = TripExpense.objects.filter(order=order).aggregate(...)          # order-keyed
fuel = FuelEntry.objects.filter(trip=order.trip).aggregate(...)            # WHOLE-trip-keyed
```

Two separate errors in two adjacent lines:

- **Fuel is over-counted.** `FuelEntry` has no order FK, only `trip` (`fleet/models.py:799`).
  On a trip carrying five orders, every one of those five orders is charged the **entire
  trip's diesel**. Diesel is the largest cost in the business, so a consolidated trip reports
  roughly 5× its true cost, five times over.
- **Expenses are under-counted.** Filtering on `order=` silently ignores every `TripExpense`
  written with only `trip` set — which is exactly what the trip-sheet endpoint at
  `fleet/views.py:210` creates, since it upserts expenses per category against the *trip*.

So the trip sheet and the order P&L, fed the same reality, disagree — and consolidation, the
feature the dispatch planner exists to produce, is precisely when they disagree most.

**Measured, not inferred.** One trip, two orders of ₹10,000 each, one ₹10,000 diesel fill and
one ₹1,000 toll booked against the trip — ₹11,000 of real cost against ₹20,000 of revenue,
so ₹9,000 of genuine margin. `GET /orders/{id}/profitability/` returns:

```
ORD-PROOF-0: fuel=10000.0  trip_expenses=0.0  total_cost=10000.0  profit=0.0
ORD-PROOF-1: fuel=10000.0  trip_expenses=0.0  total_cost=10000.0  profit=0.0
                                    sum of reported cost: 20000.0   (actual: 11000)
```

Both failures visible at once: the diesel is charged in full to **each** order, and the
trip-keyed toll is **invisible** — it never appears against any order at all.

The business consequence is the part that matters. A consolidated trip that really earned
₹9,000 reports **zero profit on every consignment**. Consolidation is the one thing the
dispatch planner exists to produce, and the P&L currently punishes it: the more orders a
dispatcher folds onto one truck, the worse that truck's per-order margin appears. Anyone
managing to this number would learn to stop consolidating.

**There is no apportionment basis anywhere in the codebase.** Nothing decides how a trip's
shared diesel should be split across the orders riding on it.

### 3.3 `TripExpense` has two parents and no rule about which wins

`TripExpense` (`fleet/models.py:830-834`) carries **both** `trip` and `order` FKs, both
nullable, with no constraint and no documented precedence. Four states are representable:
trip-only, order-only, both, neither. Different call sites write different ones, and §3.2 is
the direct consequence.

---

## 4. What this plan delivers

| # | Capability | Phase |
|---|---|---|
| 1 | An LR generated from the order, not re-typed — with a PDF | 1 |
| 2 | One documented rule for what an expense hangs off, enforced | 2 |
| 3 | Trip cost apportioned across the orders it carries, by a stated basis | 2 |
| 4 | `order_profitability` and the trip sheet agreeing, always | 2 |
| 5 | One **Trip Cockpit** API returning the whole stack for one trip | 3 |
| 6 | One Trip Cockpit **screen** — the eight screens collapsed into one | 4 |
| 7 | Consolidated invoicing: one bill for several orders on one trip | 5 |
| 8 | A trip-wise P&L **report** across trips, not just one sheet at a time | 5 |

---

## 5. Phases

### Phase 1 — The LR stops being an island — ✅ shipped

**No migration needed, on inspection.** §3.1 already names the fix: `Order.lorry_receipt`
exists as a nullable FK; nothing ever populated it. Adding a second FK the other way, as
originally drafted here, would have been redundant with a field already on the model — the
gap was never the schema, only the missing write path. Kept the existing free-text fields
on `LorryReceipt` — an LR is a legal document that must preserve what was *printed*, even if
the order is edited later.

**`fleet/lr.py` — `build_lr_from_order(order)`**, mirroring `build_invoice_from_order`
(`fleet/billing.py:41`) exactly: idempotent (a second call returns the existing LR rather
than issuing a duplicate consignment note), snapshots consignor/consignee/origin/destination/
material/weight/packages from the order at issue time, copies `eway_bill_number` and
`freight_amount`, sets `Order.lorry_receipt`, and adds the LR to `trip.lorry_receipts` so
the two no-op `update()` calls at `fleet/views.py:196,205` start doing their job.

**`POST /orders/{id}/generate-lr/`** and an LR PDF at `GET /lorry-receipts/{id}/pdf/`, built
with the `reportlab` setup already used for invoices.

**Auto-issue on dispatch**, since an LR is a legal precondition for the goods moving: call
it from `dispatch_trip` for every order on the trip that lacks one.

~350 lines, ~10 tests. Depends on nothing.

### Phase 2 — One cost, apportioned honestly — ✅ shipped

This is the highest-value phase and the only one that fixes a wrong number.

**Settle the keying rule (§3.3), corrected on inspection.** `TripExpense.trip` is the
authority when a trip exists; `order` is an optional narrowing meaning "this cost belongs
to that one consignment specifically." `TripExpense.save()` now backfills `trip` from
`order.trip` whenever one exists, and a data migration catches up existing rows. A hard
constraint rejecting "neither set" was drafted, then dropped once the tests it broke showed
why: `fleet.billing.running_cost` deliberately reads `TripExpense` by `vehicle` alone for
costs that never belonged to any one trip (an RTO fine, a permit) — a real, load-bearing
pattern the original wording here would have outlawed.

**`fleet/costing.py` — `apportion_trip_cost(trip)`**, returning each order's share of the
trip's shared cost. Basis, in order of preference, each degrading explicitly and reporting
which was used:

1. **Distance** — an order's `distance_km` over the trip's total. The fairest basis for
   diesel, which is what dominates.
2. **Weight** — `weight_kg` share, when distance is unrecorded.
3. **Equal split** — when neither is known, flagged as such rather than silently guessed.

Directly-attributed costs (a `TripExpense` with `order` set) are assigned whole and excluded
from the shared pool, so apportionment never double-charges them.

**Rewrite `order_profitability`** (`fleet/views.py:1473`) on top of it, and have it return
`cost_basis` so a reader can see whether the split was by distance, weight, or equal — the
same honesty `fleet/allocation.py` already applies to estimated vendor rates.

**Add a reconciliation test** asserting the invariant that currently fails: *the sum of every
order's apportioned cost equals the trip's total cost, exactly.* That single test is what
stops §3.2 recurring.

~400 lines, ~14 tests. Depends on nothing; unblocks 3 and 5.

### Phase 3 — The Trip Cockpit API — ✅ shipped

**`GET /trips/{id}/cockpit/`** — one response carrying the whole stack for one trip:

```
trip           number, vehicle, driver, status, load/unload dates, odometer, km
orders[]       each with its LR, POD state, invoice, and apportioned cost
costs          fuel, on-road by category, advance, attributed vs shared split
revenue        per order and total, GST separated
pnl            the settlement_summary numbers, plus cost_basis from Phase 2
documents[]    LR PDFs, POD files, invoice PDFs — one list of links
blockers[]     what stands between this trip and being closed out
```

`blockers` is the piece that earns the screen: *"2 orders have no verified POD"*, *"1 order
is unpriced"*, *"trip has no end odometer"*, *"₹4,200 of expenses unapproved"* — the checklist
an operator currently reconstructs by opening eight screens.

Builds almost entirely on existing pieces (`settlement_summary`, `order_pod_state`,
`apportion_trip_cost`); this is composition, not new logic.

**Found in passing:** `OrderViewSet.settlement` (`fleet/views.py:978`, the order-level
"four-sided settlement sheet") carried the exact same fuel/expense bug §3.2 diagnosed in
`order_profitability` - a second, independent site charging a consolidated trip's whole
diesel bill to one order. Fixed alongside the cockpit so the two sheets can never disagree
with each other again.

~300 lines, ~8 tests, plus 1 pre-existing bug fixed. Depends on 1, 2.

### Phase 4 — The Trip Cockpit screen

One screen replacing the eight-screen tour: trip header, the order/LR/POD/invoice table, the
cost stack, the P&L strip, the blocker checklist, and the actions inline — generate LR, log
expense, verify POD, raise invoice — each hitting the endpoint that already exists, without
leaving the page.

Add it to `navGroups` **and its routing branch** (`app/page.tsx`) — noting that merge-dropped
routing branch is exactly the bug that took the Scenario Profiles screen down at `ca45de3`.

~700 lines. Depends on 3.

### Phase 5 — Consolidated billing and the trip P&L report

**Consolidated invoicing.** Today `build_invoice_from_order` bills one order
(`fleet/billing.py:41`), so a five-order trip raises five invoices even for one customer on
one lane. Add `build_invoice_from_trip(trip, customer=...)` grouping a trip's orders **per
customer** into one invoice with one line per consignment, reusing the same POD gate and the
same idempotency guarantee. Order-level invoicing stays — PTL customers genuinely want a bill
per consignment; this is an additional path, not a replacement.

**`GET /reports/trip-profitability/`** — trip-wise P&L across a date range: revenue, fuel,
on-road, advance, margin, ₹/km, filterable by vehicle, driver, branch, customer and lane.
`accounting/views.py:285` already does exactly this shape for vehicles; this is the trip
equivalent, which is what the business actually manages.

~550 lines, ~12 tests. Depends on 2, 3.

---

## 6. Sequencing and effort

| Phase | Scope | Size | Depends on |
|---|---|---|---|
| **1 — LR** | `lr.py`, migration, PDF, auto-issue | ~350 lines, 10 tests | — |
| **2 — Apportionment** | `costing.py`, keying rule, P&L rewrite | ~400 lines, 14 tests | — |
| **3 — Cockpit API** | `GET /trips/{id}/cockpit/`, blockers | ~300 lines, 8 tests | 1, 2 |
| **4 — Cockpit screen** | One screen, inline actions | ~700 lines | 3 |
| **5 — Billing + report** | Consolidated invoice, trip P&L report | ~550 lines, 12 tests | 2, 3 |

**Recommended order: 2 → 1 → 3 → 4 → 5.**

Phase 2 goes first despite Phase 1 being the more visible gap, because Phase 2 is the only
one that fixes a number that is currently *wrong* — and every P&L figure in Phases 3, 4 and 5
is built on it. Shipping the cockpit on top of an un-apportioned cost model would put a
five-times-overstated diesel figure on a screen designed to be trusted.

Phases 1 and 2 are independent of each other and can run in parallel.

Every phase keeps `USE_SQLITE=true … manage.py test` green (563 tests at `ca45de3`).

---

## 7. What this plan does not cover

Stated so the boundary is explicit rather than discovered later:

- **Driver settlement and payout.** `Settlement` (`fleet/models.py:253`) stays as it is —
  advance vs approved expenses. Making it a real payout run (approval workflow, bank file,
  TDS) is its own piece of work.
- **E-way bill generation or the NIC API.** `eway_bill_number` remains a field an operator
  types; this plan carries it onto the LR, it does not generate it.
- **Rewriting the LR's free-text fields into FKs.** Deliberate: an issued consignment note
  must keep what was printed on it, even after the order is edited.
- **Multi-currency, multi-branch inter-company settlement.** Out of scope throughout.
- **Retrofitting history.** Apportionment applies to trips computed after Phase 2; historical
  `order_profitability` figures are not restated in place. A one-off backfill command is
  possible but is not scoped here.
- **The dispatch planner itself.** Untouched — this plan consumes the trips it produces.
