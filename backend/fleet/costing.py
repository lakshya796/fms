"""Splitting one trip's shared cost across the orders riding on it.

A trip can carry many orders - that is the point of `Trip.add_order` and of the
dispatch planner's milk-run consolidation. Diesel and most on-road expense are
booked once against the *trip*, not against any one order, so before this module
existed nothing decided how that shared cost should be divided among the
consignments it actually moved. See docs/ONE-TRIP-END-TO-END.md §3.2/§5 Phase 2.
"""
from decimal import Decimal

from django.db.models import Sum

from .models import FuelEntry, TripExpense, money


def _split_by_share(amount, weights, total_weight, order_ids):
    """Split `amount` across `order_ids` proportionally to `weights`, giving the
    last order the remainder so the parts always sum to `amount` exactly despite
    rounding - the reconciliation invariant this module exists to guarantee."""
    out = {}
    allocated = Decimal("0")
    for index, order_id in enumerate(order_ids):
        if index == len(order_ids) - 1:
            out[order_id] = money(amount - allocated)
        else:
            share = money(amount * weights[order_id] / total_weight) if total_weight else Decimal("0")
            out[order_id] = share
            allocated += share
    return out


def apportion_trip_cost(trip):
    """Split `trip`'s shared cost - its diesel, plus any trip-keyed expense not
    attributed to one order - across the orders it carries.

    Basis, in order of preference, each reported explicitly so a reader knows
    whether a number was measured or guessed (`fleet.allocation` already applies
    this honesty to estimated vendor rates):

    1. **distance** - an order's `distance_km` share of the trip's total. The
       fairest basis for diesel, which is what dominates.
    2. **weight** - `weight_kg` share, when no order on the trip has a distance.
    3. **equal** - split evenly, when neither is known.

    An expense with `order` set is charged to that order in full and never enters
    the shared pool, however it was recorded - `TripExpense.save` keeps `trip` in
    sync with `order.trip`, so it is still counted in the trip's own totals.

    Returns `{"basis": ..., "shared_fuel": Decimal, "shared_expenses": Decimal,
    "shared_total": Decimal, "orders": {order_id: {"fuel": Decimal,
    "shared_expenses": Decimal, "attributed_expenses": Decimal, "total_cost":
    Decimal}}}`. `orders` covers every order on the trip, including ones with no
    cost of their own.
    """
    orders = list(trip.orders.all())
    shared_fuel = money(FuelEntry.objects.filter(trip=trip).aggregate(v=Sum("amount"))["v"] or 0)
    shared_expenses = money(TripExpense.objects.filter(trip=trip, order__isnull=True).aggregate(v=Sum("amount"))["v"] or 0)
    shared_total = shared_fuel + shared_expenses

    if not orders:
        return {"basis": "none", "shared_fuel": shared_fuel, "shared_expenses": shared_expenses,
               "shared_total": shared_total, "orders": {}}

    order_ids = [order.id for order in orders]
    attributed = {order.id: money(TripExpense.objects.filter(order=order).aggregate(v=Sum("amount"))["v"] or 0)
                 for order in orders}

    distances = {order.id: money(order.distance_km or 0) for order in orders}
    total_distance = sum(distances.values())
    weights = {order.id: money(order.weight_kg or 0) for order in orders}
    total_weight = sum(weights.values())

    if total_distance > 0:
        basis, keys, total_key = "distance", distances, total_distance
    elif total_weight > 0:
        basis, keys, total_key = "weight", weights, total_weight
    else:
        basis, keys, total_key = "equal", {order_id: Decimal("1") for order_id in order_ids}, Decimal(len(order_ids))

    fuel_shares = _split_by_share(shared_fuel, keys, total_key, order_ids)
    expense_shares = _split_by_share(shared_expenses, keys, total_key, order_ids)

    result = {}
    for order_id in order_ids:
        result[order_id] = {
            "fuel": fuel_shares[order_id],
            "shared_expenses": expense_shares[order_id],
            "attributed_expenses": attributed[order_id],
            "total_cost": money(fuel_shares[order_id] + expense_shares[order_id] + attributed[order_id]),
        }
    return {"basis": basis, "shared_fuel": shared_fuel, "shared_expenses": shared_expenses,
           "shared_total": shared_total, "orders": result}
