"""The lorry receipt, generated from the order it documents rather than re-typed
by hand.

`Order.lorry_receipt` (`fleet/models.py`) has existed as a nullable FK since the
Order model was written, but no application flow ever populated it - an operator
re-typed consignor, consignee, origin, destination, material and weight into a
separate `LorryReceipt` record instead. `build_lr_from_order` is what populates it.
See docs/ONE-TRIP-END-TO-END.md §3.1/§5 Phase 1.
"""
from uuid import uuid4

from django.utils import timezone

from .models import LorryReceipt, money


def lr_number():
    return "LR-" + timezone.now().strftime("%y%m%d") + uuid4().hex[:6].upper()


def build_lr_from_order(order):
    """Raise (or return) the lorry receipt for a consignment.

    Idempotent: a second call returns the LR already issued against the order
    rather than raising a duplicate consignment note - the same guarantee
    `build_invoice_from_order` (`fleet/billing.py`) gives for the invoice.

    The LR's own fields are a snapshot at issue time, not a live view of the
    order: an LR is a legal document that must keep what was printed on it,
    even if the order is edited afterwards.
    """
    if order.lorry_receipt_id:
        return order.lorry_receipt, False

    consignor = order.pickup.contact_name or order.customer.name
    consignee = order.dropoff.contact_name or order.dropoff.name
    lr = LorryReceipt.objects.create(
        number=lr_number(), customer=order.customer,
        consignor=consignor, consignee=consignee,
        origin=order.pickup.city or order.pickup.name, destination=order.dropoff.city or order.dropoff.name,
        material=order.payload_description or "General cargo",
        weight_kg=order.weight_kg, packages=order.packages or 1,
        eway_bill_number=order.eway_bill_number, freight_amount=money(order.freight_amount))
    order.lorry_receipt = lr
    order.save(update_fields=["lorry_receipt", "updated_at"])
    if order.trip_id:
        order.trip.lorry_receipts.add(lr)
    return lr, True
