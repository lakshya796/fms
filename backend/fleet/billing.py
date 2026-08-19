"""Money derived from operations rather than typed in by hand.

Two things live here:

* `build_invoice_from_order` — a customer invoice whose freight, GST and total come
  straight from the consignment's rate card, so the bill can never drift from what
  was quoted and delivered.
* `project_lane` — what a lane would earn and what it would cost to run, using the
  fleet's own recorded diesel and on-road spend rather than an assumed figure.
"""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.db.models import Avg, DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone

from .models import FuelEntry, Invoice, TripExpense, money

# Fallbacks used only when a fleet has no history to learn from yet.
DEFAULT_DIESEL_PRICE = Decimal("94.00")   # rupees per litre
DEFAULT_MILEAGE_KMPL = Decimal("4.00")    # a laden multi-axle truck


class BillingError(Exception):
    """Raised when a consignment is not in a state that can be billed."""


def invoice_number():
    return "INV-" + timezone.now().strftime("%y%m%d") + uuid4().hex[:6].upper()


def order_invoice(order):
    """The invoice covering `order`, whichever of the two billing paths raised
    it: singly (`Invoice.order`, `build_invoice_from_order`) or as part of a
    consolidated trip invoice (`Invoice.orders`, `build_invoice_from_trip`).
    Every reader that asks "has this order been billed" - the Trip Cockpit,
    the order settlement sheet, this module's own idempotency checks - must
    go through here, or one of the two paths silently stops recognising the
    other's invoice."""
    return order.invoices.order_by("created_at").first() or order.consolidated_invoices.order_by("created_at").first()


def order_pod_state(order):
    """Where the consignment's ePOD stands: (captured, verified)."""
    proofs = order.proofs.all()
    captured = any(proof.captured_at for proof in proofs)
    verified = any(proof.status == "verified" for proof in proofs)
    return captured, verified


def build_invoice_from_order(order, *, due_days=15, additional_charges=None, created_by=""):
    """Raise (or return) the invoice for a delivered consignment.

    Idempotent: a second call returns the invoice already raised against the order
    rather than billing the customer twice - including one raised as part of a
    consolidated trip invoice (`build_invoice_from_trip`), not just a single-order one.
    """
    existing = order_invoice(order)
    if existing:
        return existing, False
    if order.status == "cancelled":
        raise BillingError("A cancelled consignment cannot be invoiced.")
    if order.status != "completed":
        raise BillingError("Only a delivered consignment can be invoiced. Complete it first.")
    if order.pod_required and not order_pod_state(order)[1]:
        raise BillingError("The ePOD for this consignment has not been verified yet, so it cannot be billed.")

    # Always reprice from the rate card, so a mid-contract rate revision is picked up.
    if order.service_rate:
        order.price_from_rate_card()
    if not order.total_amount:
        raise BillingError("This consignment has no freight against it. Price it from a rate card first.")

    extras = money(order.other_charges) + money(additional_charges or 0)
    invoice = Invoice(
        number=invoice_number(), customer=order.customer, trip=order.trip, order=order,
        freight_amount=money(order.freight_amount), additional_charges=extras,
        tax_amount=money(order.tax_amount),
        gst_percent=order.service_rate.gst_percent if order.service_rate else Decimal("0"),
        reverse_charge=bool(order.service_rate and order.service_rate.reverse_charge),
        place_of_supply=order.dropoff.state or order.pickup.state,
        due_date=timezone.localdate() + timedelta(days=int(due_days or 0)),
        status="raised")
    invoice.save()   # total_amount is recomputed in Invoice.save
    order.log(order.status, "INVOICE_RAISED",
              f"Invoice {invoice.number} for {invoice.total_amount}", city=order.dropoff.city)
    return invoice, True


def build_invoice_from_trip(trip, *, due_days=15):
    """Consolidate every billable, not-yet-invoiced order on `trip` into one
    invoice per customer - a single bill for a milk run, instead of one
    invoice per consignment even when several ride the same truck for the
    same customer on the same trip.

    Reuses the same gates as `build_invoice_from_order` (delivered, priced,
    ePOD verified) and the same idempotency guarantee: an order already
    billed, singly or as part of an earlier consolidated invoice, is skipped
    rather than billed twice - which is also why an order billed here is
    invisible to a later `build_invoice_from_order` call, and vice versa.

    Returns `(invoices, skipped)` - `invoices` is `[(Invoice, created), ...]`,
    one per customer with at least one billable order; `skipped` is
    `[(order, reason), ...]` for every order left out, so a dispatcher sees
    why a consignment did not make it onto the bill instead of it silently
    vanishing.
    """
    by_customer = {}
    for order in trip.orders.select_related("customer", "service_rate", "pickup", "dropoff").order_by("id"):
        by_customer.setdefault(order.customer_id, []).append(order)

    invoices, skipped = [], []
    for customer_orders in by_customer.values():
        billable = []
        for order in customer_orders:
            if order_invoice(order):
                continue   # already billed - not an error, just nothing left to do for it here
            if order.status == "cancelled":
                skipped.append((order, "cancelled")); continue
            if order.status != "completed":
                skipped.append((order, "not yet delivered")); continue
            if order.pod_required and not order_pod_state(order)[1]:
                skipped.append((order, "ePOD not verified")); continue
            if order.service_rate:
                order.price_from_rate_card()
            if not order.total_amount:
                skipped.append((order, "not priced")); continue
            billable.append(order)
        if not billable:
            continue

        freight = money(sum((money(o.freight_amount) for o in billable), Decimal("0")))
        extras = money(sum((money(o.other_charges) for o in billable), Decimal("0")))
        tax = money(sum((money(o.tax_amount) for o in billable), Decimal("0")))
        rate = billable[0].service_rate
        line_items = [{"order_id": o.id, "order_number": o.number,
                       "route": f"{o.pickup.city} → {o.dropoff.city}",
                       "freight_amount": float(money(o.freight_amount)),
                       "tax_amount": float(money(o.tax_amount)),
                       "total_amount": float(money(o.total_amount))} for o in billable]
        invoice = Invoice(
            number=invoice_number(), customer=billable[0].customer, trip=trip,
            freight_amount=freight, additional_charges=extras, tax_amount=tax,
            gst_percent=rate.gst_percent if rate else Decimal("0"),
            reverse_charge=bool(rate and rate.reverse_charge),
            place_of_supply=billable[0].dropoff.state or billable[0].pickup.state,
            due_date=timezone.localdate() + timedelta(days=int(due_days or 0)),
            status="raised", line_items=line_items)
        invoice.save()
        invoice.orders.set(billable)
        for order in billable:
            order.log(order.status, "INVOICE_RAISED",
                      f"Consolidated invoice {invoice.number} for {order.total_amount}", city=order.dropoff.city)
        invoices.append((invoice, True))
    return invoices, skipped


def running_cost(vehicle=None, days=90):
    """What a kilometre actually costs this fleet, from recorded fuel and on-road spend.

    Falls back to sensible national figures when a vehicle (or the whole fleet) has
    no history — a projection should still be useful on day one.
    """
    since = timezone.localdate() - timedelta(days=days)
    fuel = FuelEntry.objects.filter(entry_date__gte=since)
    expenses = TripExpense.objects.filter(expense_date__gte=since)
    if vehicle is not None:
        fuel = fuel.filter(vehicle=vehicle)
        expenses = expenses.filter(vehicle=vehicle)

    totals = fuel.aggregate(
        litres=Sum("volume_litres"), spend=Sum("amount"),
        mileage=Avg("mileage_kmpl", filter=Q(mileage_kmpl__gt=0)),
        # Distance covered is only known for a fill that had a previous odometer reading
        # to measure against; the first fill of a vehicle contributes fuel but no km.
        km=Sum(ExpressionWrapper(F("mileage_kmpl") * F("volume_litres"), output_field=DecimalField())))
    litres = money(totals["litres"] or 0)
    spend = money(totals["spend"] or 0)
    mileage = money(totals["mileage"] or 0) or DEFAULT_MILEAGE_KMPL
    diesel_price = money(spend / litres) if litres else DEFAULT_DIESEL_PRICE
    km_run = money(totals["km"] or 0)
    on_road = money(expenses.aggregate(value=Sum("amount"))["value"] or 0)
    return {
        "sample_days": days,
        "diesel_price": diesel_price,
        "mileage_kmpl": mileage,
        "km_run": km_run,
        "fuel_spend": spend,
        "on_road_spend": on_road,
        "fuel_cost_per_km": money(diesel_price / mileage),
        "on_road_cost_per_km": money(on_road / km_run) if km_run else Decimal("0"),
        "from_history": bool(litres),
    }


def project_lane(rate, *, distance_km, weight_kg=0, hours=0, halt_days=0, other_charges=0,
                 trips_per_month=1, vehicle=None, diesel_price=None, mileage_kmpl=None, days=90):
    """Revenue, cost and margin for a lane before anyone commits a truck to it.

    GST is excluded from revenue — it is collected on behalf of the government, not
    earned — so the margin here is the one that actually reaches the owner.
    """
    distance = money(distance_km or 0)
    breakdown = rate.quote(distance_km=distance, weight_kg=weight_kg, hours=hours,
                           halt_days=halt_days, other_charges=other_charges)
    basis = running_cost(vehicle=vehicle, days=days)
    if diesel_price:
        basis["diesel_price"] = money(diesel_price)
    if mileage_kmpl:
        basis["mileage_kmpl"] = money(mileage_kmpl)
    basis["fuel_cost_per_km"] = money(basis["diesel_price"] / (basis["mileage_kmpl"] or DEFAULT_MILEAGE_KMPL))

    revenue = money(breakdown["taxable_value"])
    fuel_cost = money(basis["fuel_cost_per_km"] * distance)
    on_road_cost = money(basis["on_road_cost_per_km"] * distance)
    total_cost = money(fuel_cost + on_road_cost)
    margin = money(revenue - total_cost)
    trips = int(trips_per_month or 1)
    return {
        "breakdown": breakdown,
        "basis": {key: float(value) if isinstance(value, Decimal) else value for key, value in basis.items()},
        "distance_km": float(distance),
        "revenue": float(revenue),
        "gst_amount": breakdown["gst_amount"],
        "billed_total": breakdown["total"],
        "fuel_cost": float(fuel_cost),
        "on_road_cost": float(on_road_cost),
        "total_cost": float(total_cost),
        "margin": float(margin),
        "margin_percent": float(round(margin / revenue * 100, 2)) if revenue else 0.0,
        "revenue_per_km": float(money(revenue / distance)) if distance else 0.0,
        "cost_per_km": float(money(total_cost / distance)) if distance else 0.0,
        "break_even_rate_per_km": float(money(total_cost / distance)) if distance else 0.0,
        "trips_per_month": trips,
        "monthly": {
            "revenue": float(money(revenue * trips)),
            "cost": float(money(total_cost * trips)),
            "margin": float(money(margin * trips)),
        },
    }
