"""Splitting one trip's shared freight across the orders riding on it - the
revenue-side mirror of `fleet.costing`, which does the same for cost.

Multi-point delivery was priced as though each drop were the only consignment
on the truck: a `per_trip` rate card, or the base/minimum/loading component of
a per-km card, was charged in full against every order on a consolidated trip
and then summed by `build_invoice_from_trip`, rather than charged once for the
journey and divided among the consignments that rode it. See
docs/MULTIPOINT-FREIGHT.md for the defect measured and the design this
implements.

The rule, stated once (docs/MULTIPOINT-FREIGHT.md §3): a charge that belongs
to the journey is levied once per trip and divided among the consignments on
it; a charge that belongs to a consignment is levied on that consignment; an
order with its own rate card is priced from it; no figure is ever counted
twice.
"""
from decimal import Decimal

from .costing import _split_by_share
from .models import LorryReceipt, Order, money


def _trip_orders(trip):
    """`Order.objects.filter(trip=trip)`, not `trip.orders.all()`: a caller
    that fetched `trip` through a prefetched queryset (the trip API views all
    do) has a stale `orders` prefetch cache the moment an order joins or
    leaves the trip in the same request - this always hits the database."""
    return Order.objects.filter(trip=trip)


def quote_scoped(rate, *, scope, distance_km=0, weight_kg=0, hours=0, halt_days=0):
    """The slice of a `ServiceRate.quote()`-style breakdown that belongs to
    `scope` ('trip' or 'consignment'), classified per §3.1:

    - `per_km_rate` / `per_ton_km_rate` / `per_kg_rate` core and the
      `unloading_charge` are always consignment-scoped - they scale with, or
      are performed once for, this specific drop.
    - `base_charge`, the `per_hour_rate` core and `halting_charge_per_day` are
      trip-scoped by default (`rate.fixed_charge_scope == "per_trip"`, the
      right semantics for a lorry rate card) or consignment-scoped when the
      card is flagged `per_consignment` (the escape hatch for a parcel/PTL
      card whose "base charge" is really a per-booking fee).
    - `loading_charge` is always trip-scoped - one truck is loaded once.

    `minimum_charge` is deliberately excluded: it is a floor on the *trip's*
    combined freight, not on either scope alone, so it is applied once in
    `apportion_trip_freight` after both scopes are computed - see there for
    why. `other_charges` is excluded too: it is an amount typed directly onto
    one order and is never pooled or divided.

    Summing `quote_scoped(rate, scope="trip", **kw)` and `quote_scoped(rate,
    scope="consignment", **kw)` component-for-component reproduces
    `rate.quote(**kw)`'s pre-minimum freight, surcharge base and handling
    exactly - the backward-compatibility guarantee for a solo order on a trip
    with nobody to share with.
    """
    distance = Decimal(str(distance_km or 0))
    weight = Decimal(str(weight_kg or 0))
    fixed_here = (scope == "trip") == (rate.fixed_charge_scope == "per_trip")

    core = Decimal("0")
    if scope == "consignment":
        if rate.rate_type == "per_km":
            core = rate.per_km_rate * distance
        elif rate.rate_type == "per_ton_km":
            core = rate.per_ton_km_rate * (weight / Decimal("1000")) * distance
        elif rate.rate_type == "per_kg":
            core = rate.per_kg_rate * weight
    if rate.rate_type == "per_hour" and fixed_here:
        core += rate.per_hour_rate * Decimal(str(hours or 0))

    base = rate.base_charge if fixed_here else Decimal("0")
    freight = money(core + base)

    halting = money(rate.halting_charge_per_day * Decimal(str(halt_days or 0))) if fixed_here else Decimal("0")
    if scope == "consignment":
        handling = money(rate.unloading_charge + halting)
    else:
        handling = money(rate.loading_charge + halting)

    return {"freight": freight, "handling_charges": handling}


def _apportion_weights(orders, basis):
    """The same distance -> weight -> equal ladder `apportion_trip_cost` uses,
    each reported explicitly so a reader knows whether a number was measured
    or assumed. `basis` forces a rung; `None` walks the ladder."""
    ids = [o.id for o in orders]
    if basis == "equal":
        return {i: Decimal("1") for i in ids}, Decimal(len(ids)), "equal"

    distances = {o.id: money(o.distance_km or 0) for o in orders}
    total_distance = sum(distances.values())
    if basis in (None, "distance") and total_distance > 0:
        return distances, total_distance, "distance"

    weights = {o.id: money(o.weight_kg or 0) for o in orders}
    total_weight = sum(weights.values())
    if basis in (None, "weight") and total_weight > 0:
        return weights, total_weight, "weight"

    return {i: Decimal("1") for i in ids}, Decimal(len(ids)), "equal"


def apportion_trip_freight(trip, *, basis=None):
    """Split `trip`'s journey-level freight across the orders riding on it.

    Orders are grouped by the `ServiceRate` they share. Within a group, each
    order's own consignment-scoped components are computed from its own
    inputs and never pooled; the group's trip-scoped components (and any
    `minimum_charge` top-up, applied once to the group's combined total) are
    computed once and divided by `basis` using the same remainder-preserving
    split `apportion_trip_cost` uses, so the shares always sum to the trip
    charge exactly despite rounding.

    GST is computed per order on that order's own combined total - which is
    how a GST invoice line is actually computed, not a shortcut - so it needs
    no further reconciliation.

    An order with no rate card mapped is left exactly as it is: this
    apportions what a rate card produces, it does not invent a price for one
    that has none.

    Returns `{"basis": str, "orders": {order_id: {"own": Decimal, "share":
    Decimal, "freight_amount": Decimal, "tax_amount": Decimal, "total_amount":
    Decimal, "source": "rate_card" | "trip_share" | "mixed" | "manual"}}}`.
    """
    orders = list(_trip_orders(trip).select_related("service_rate").order_by("id"))
    if not orders:
        return {"basis": basis or "none", "orders": {}}

    by_rate = {}
    for order in orders:
        by_rate.setdefault(order.service_rate_id, []).append(order)

    result = {}
    chosen_basis = None
    for rate_id, group in by_rate.items():
        if rate_id is None:
            for order in group:
                result[order.id] = {
                    "own": money(order.freight_amount), "share": Decimal("0"),
                    "freight_amount": money(order.freight_amount), "tax_amount": money(order.tax_amount),
                    "total_amount": money(order.total_amount), "source": "manual",
                }
            continue

        rate = group[0].service_rate
        own = {o.id: quote_scoped(rate, scope="consignment", distance_km=o.distance_km, weight_kg=o.weight_kg)
              for o in group}
        own_freight = {oid: bd["freight"] for oid, bd in own.items()}
        own_handling = {oid: bd["handling_charges"] for oid, bd in own.items()}

        trip_bd = quote_scoped(rate, scope="trip")
        pre_minimum = money(sum(own_freight.values(), Decimal("0")) + trip_bd["freight"])
        topup = Decimal("0")
        if rate.minimum_charge and pre_minimum < rate.minimum_charge:
            topup = money(rate.minimum_charge - pre_minimum)
        trip_freight = money(trip_bd["freight"] + topup)
        trip_handling = trip_bd["handling_charges"]

        ids = [o.id for o in group]
        weights, total_weight, basis_used = _apportion_weights(group, basis)
        chosen_basis = chosen_basis or basis_used
        freight_shares = _split_by_share(trip_freight, weights, total_weight, ids)
        handling_shares = _split_by_share(trip_handling, weights, total_weight, ids)

        for order in group:
            freight = money(own_freight[order.id] + freight_shares[order.id])
            handling = money(own_handling[order.id] + handling_shares[order.id])
            surcharge = money(freight * rate.fuel_surcharge_percent / Decimal("100"))
            freight_amount = money(freight + surcharge + handling)
            other = money(order.other_charges)
            taxable = money(freight + surcharge + handling + other)
            tax_amount = Decimal("0") if rate.reverse_charge else money(taxable * rate.gst_percent / Decimal("100"))
            total_amount = money(taxable + tax_amount)

            share = money(freight_shares[order.id] + handling_shares[order.id])
            own_total = money(own_freight[order.id] + own_handling[order.id])
            if len(group) == 1 or share == 0:
                source = "rate_card"
            elif own_total == 0:
                source = "trip_share"
            else:
                source = "mixed"

            result[order.id] = {"own": own_total, "share": share, "freight_amount": freight_amount,
                                "tax_amount": tax_amount, "total_amount": total_amount, "source": source}

    return {"basis": chosen_basis or "none", "orders": result}


def reconcile_trip_freight(trip):
    """The §3.3 invariant as a callable: does what every order on `trip` is
    currently storing match what apportioning the trip's rate cards would
    produce right now? A mismatch means the trip's shape (an order added or
    removed, a distance or weight changed, a rate card swapped) has moved
    since anything on it was last priced - `recalculate-freight` is how a
    reader closes the gap.
    """
    split = apportion_trip_freight(trip)
    orders = {o.id: o for o in _trip_orders(trip)}
    if not orders:
        return {"balanced": True, "expected": 0.0, "actual": 0.0, "difference": 0.0, "stale_orders": []}

    expected = money(sum((row["total_amount"] for row in split["orders"].values()), Decimal("0")))
    actual = money(sum((o.total_amount for o in orders.values()), Decimal("0")))
    stale = [oid for oid, row in split["orders"].items()
            if row["source"] != "manual" and money(orders[oid].total_amount) != row["total_amount"]]
    return {"balanced": not stale, "expected": float(expected), "actual": float(actual),
           "difference": float(money(expected - actual)), "stale_orders": stale}


def price_trip_orders(trip, *, basis=None, force=False, save=True):
    """Recompute every order on `trip` via `apportion_trip_freight` and
    (unless `save=False`, a preview) write the result back.

    An order that already carries an invoice is skipped unless `force` is
    set - repricing it changes what was billed without correcting the bill,
    which is a credit/debit note, not something this function does.

    A repriced order's lorry receipt (if it has one) is refreshed too - see
    `fleet.lr.sync_lorry_receipt_freight`. An LR is otherwise a frozen
    snapshot from whenever it was issued; without this, an LR generated
    before its order was priced shows zero freight forever, surviving even a
    correct recalculation of the order behind it.

    Returns the before/after table the `recalculate-freight` endpoint and its
    console preview render: `{"basis": str, "orders": [{"id", "number",
    "before", "after", "delta", "source", "blocked"}], "total_before":
    float, "total_after": float, "delta": float, "lorry_receipts_synced":
    [str, ...]}`.
    """
    split = apportion_trip_freight(trip, basis=basis)
    orders = {o.id: o for o in _trip_orders(trip)}
    rows = []
    to_save = []
    for oid, row in split["orders"].items():
        order = orders[oid]
        before = money(order.total_amount)
        already_invoiced = order.invoices.exists() or order.consolidated_invoices.exists()
        blocked = "already invoiced" if (already_invoiced and not force) else None
        skip = blocked is not None or row["source"] == "manual"
        after = before if skip else row["total_amount"]
        rows.append({"id": order.id, "number": order.number, "before": float(before),
                    "after": float(after), "delta": float(money(after - before)),
                    "source": row["source"], "blocked": blocked})
        if save and not skip:
            order.freight_amount = row["freight_amount"]
            order.tax_amount = row["tax_amount"]
            order.total_amount = row["total_amount"]
            order.freight_source = row["source"]
            to_save.append(order)

    if save and to_save:
        for order in to_save:
            order.save(update_fields=["freight_amount", "tax_amount", "total_amount",
                                      "freight_source", "updated_at"])

    lr_numbers_synced = []
    if save and to_save:
        from .lr import sync_lorry_receipt_freight
        lr_ids = {order.lorry_receipt_id for order in to_save if order.lorry_receipt_id}
        for lr in LorryReceipt.objects.filter(id__in=lr_ids):
            _, lr_before, lr_after = sync_lorry_receipt_freight(lr)
            if lr_after != lr_before:
                lr_numbers_synced.append(lr.number)

    total_before = money(sum((Decimal(str(r["before"])) for r in rows), Decimal("0")))
    total_after = money(sum((Decimal(str(r["after"])) for r in rows), Decimal("0")))
    return {"basis": split["basis"], "orders": rows, "total_before": float(total_before),
           "total_after": float(total_after), "delta": float(money(total_after - total_before)),
           "lorry_receipts_synced": lr_numbers_synced}
