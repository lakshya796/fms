"""What a hired truck actually costs, and the bill that settles it.

`VehicleHire` carries the terms agreed with the transport owner; this module turns
those terms plus whatever was approved against the trip into a payable, mirroring
the shape of `billing.build_invoice_from_order` on the customer side.
"""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.db.models import Sum
from django.utils import timezone

from .models import TripExpense, money


class HireBillingError(Exception):
    """Raised when a hire is not in a state a bill can be raised from."""


def _base_amount(hire):
    """The agreed rate resolved against what actually happened on the trip."""
    order = hire.order
    if hire.rate_basis == "trip":
        return money(hire.agreed_rate)
    if hire.rate_basis == "km":
        return money(hire.agreed_rate * (order.distance_km or 0))
    if hire.rate_basis == "ton":
        return money(hire.agreed_rate * (order.weight_kg or 0) / Decimal("1000"))
    if hire.rate_basis == "day":
        days = 1
        if order.dispatched_at and order.completed_at:
            days = max(1, (order.completed_at.date() - order.dispatched_at.date()).days + 1)
        return money(hire.agreed_rate * days)
    return money(hire.agreed_rate)


def _detention_amount(hire, detention_days=None):
    """Detention beyond the free period, in whole days billed at the agreed rate.

    `detention_days` is the number of chargeable days already net of the free
    period; pass it explicitly once waypoint dwell time is tracked (Phase 3).
    Left unset, no detention is charged rather than guessed.
    """
    if not detention_days:
        return Decimal("0")
    return money(Decimal(str(detention_days)) * hire.detention_rate_per_day)


def _approved_extras(hire):
    """On-road costs this fleet approved and agreed to pay this vendor for."""
    if not hire.trip_id:
        return Decimal("0")
    total = TripExpense.objects.filter(trip_id=hire.trip_id, vendor_id=hire.vendor_id, status="approved") \
                               .aggregate(value=Sum("amount"))["value"]
    return money(total or 0)


def vendor_payable(hire, *, detention_days=None):
    """The full payable breakdown for a hire: what is owed, what has already been
    paid as an advance, and the balance - the ladder the spec asks for.
    """
    base = _base_amount(hire)
    detention = _detention_amount(hire, detention_days)
    extras = money(hire.loading_charge + hire.unloading_charge + hire.other_charges) + _approved_extras(hire)
    gross = money(base + detention + extras)
    deductions = money(hire.deductions)
    taxable = money(gross - deductions)
    tds_rate = hire.vendor.tds_percent or Decimal("0")
    tds_amount = money(gross * tds_rate / Decimal("100"))
    total_payable = money(taxable - tds_amount)
    advance = money(hire.advance_amount)
    balance_due = money(total_payable - advance)
    return {
        "hire_id": hire.pk, "vendor": hire.vendor.name, "rate_basis": hire.rate_basis,
        "base_amount": float(base), "detention_amount": float(detention), "extra_charges": float(extras),
        "gross_amount": float(gross), "deductions": float(deductions), "taxable_amount": float(taxable),
        "tds_percent": float(tds_rate), "tds_amount": float(tds_amount), "total_payable": float(total_payable),
        "advance_amount": float(advance), "balance_due": float(balance_due),
    }


def confirmation_email(hire):
    """The vendor confirmation the spec asks for: everything the transport owner
    needs to put a truck on the road, in one message.
    """
    order = hire.order
    vehicle = order.vehicle
    driver = order.driver
    lines = [
        f"Order: {order.number}", f"Customer: {order.customer.name}",
        f"Origin: {order.pickup.name} ({order.pickup.city})", f"Destination: {order.dropoff.name} ({order.dropoff.city})",
        f"Reporting: {order.scheduled_at.strftime('%d-%b-%Y %H:%M') if order.scheduled_at else 'To be advised'}",
        f"Material: {order.payload_description or '-'}", f"Quantity/weight: {order.weight_kg} kg",
    ]
    if hire.outside_vehicle_number:
        lines.append(f"Vehicle: {hire.outside_vehicle_number} ({hire.outside_vehicle_type or 'as agreed'}, "
                     f"{hire.outside_capacity_kg or '-'} kg)")
    elif vehicle:
        lines.append(f"Vehicle: {vehicle.registration_number} ({vehicle.vehicle_type}, {vehicle.capacity_kg} kg)")
    if hire.driver_name:
        lines.append(f"Driver: {hire.driver_name} ({hire.driver_phone or '-'})")
    elif driver:
        lines.append(f"Driver: {driver.name} ({driver.phone})")
    lines.append(f"Route: {'Multi-drop / milk run' if order.waypoints.count() > 1 else 'Direct'}"
                + (f", {order.waypoints.count()} delivery point(s)" if order.waypoints.count() > 1 else ""))
    if order.special_instructions:
        lines.append(f"Instructions: {order.special_instructions}")
    lines.append(f"Agreed rate: Rs. {hire.agreed_rate} ({hire.get_rate_basis_display()}, {hire.get_hire_type_display()})")
    if hire.advance_amount:
        lines.append(f"Advance: Rs. {hire.advance_amount}")
    subject = f"Vehicle confirmed - Order {order.number} - {order.pickup.city} to {order.dropoff.city}"
    return subject, "\n".join(lines)


def raise_vendor_bill(hire, *, detention_days=None, created_by=""):
    """Bill the vendor for this hire. Idempotent per hire, like the customer side."""
    from accounting.models import VendorBill  # local import: accounting depends on fleet, not the other way round

    existing = hire.bills.order_by("created_at").first() if hasattr(hire, "bills") else None
    if existing:
        return existing, False
    if hire.status == "cancelled":
        raise HireBillingError("A cancelled hire cannot be billed.")
    payable = vendor_payable(hire, detention_days=detention_days)
    bill = VendorBill(
        number="VB-" + timezone.now().strftime("%y%m%d") + uuid4().hex[:6].upper(),
        vendor=hire.vendor, hire=hire, bill_date=timezone.localdate(),
        due_date=timezone.localdate() + timedelta(days=hire.payment_terms_days),
        taxable_amount=money(payable["taxable_amount"]), gst_amount=Decimal("0"),
        tds_amount=money(payable["tds_amount"]), paid_amount=money(payable["advance_amount"]),
        narration=f"Vendor hire for order {hire.order.number}", status="raised")
    bill.save()
    hire.status = "billed"
    hire.save(update_fields=["status", "updated_at"])
    return bill, True
