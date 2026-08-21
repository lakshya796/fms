"""The lorry receipt, generated from the order it documents rather than re-typed
by hand.

`Order.lorry_receipt` (`fleet/models.py`) has existed as a nullable FK since the
Order model was written, but no application flow ever populated it - an operator
re-typed consignor, consignee, origin, destination, material and weight into a
separate `LorryReceipt` record instead. `build_lr_from_order` is what populates it.
See docs/ONE-TRIP-END-TO-END.md §3.1/§5 Phase 1.
"""
from decimal import Decimal
from uuid import uuid4

from django.utils import timezone

from .models import LorryReceipt, money


def lr_number():
    return "LR-" + timezone.now().strftime("%y%m%d") + uuid4().hex[:6].upper()


def build_lr_from_order(order, number=None):
    """Raise (or return) the lorry receipt for a consignment.

    ``number`` may be supplied when an operator needs to preserve a pre-printed
    or otherwise externally assigned LR number. When it is omitted, the normal
    system-generated number is used.

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
        number=number or lr_number(), customer=order.customer,
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


def sync_lorry_receipt_freight(lr, save=True):
    """Refresh `lr.freight_amount` from the order(s) linked to it right now.

    The snapshot above is deliberate - an LR is a legal document, not a live
    view of the order - but a snapshot taken before the order was ever priced,
    or before a multi-point trip's freight was correctly divided among the
    orders riding it (see `fleet.freight`), freezes at the wrong number and
    nothing revisits it on its own: an LR generated at dispatch time, ahead of
    pricing, shows zero freight forever. This is the explicit correction,
    called when an order behind an LR has just been (re)priced - not a silent
    background rewrite of an issued document.

    `lr.freight_amount` becomes the sum of every order currently linked to it
    (ordinarily exactly one - `build_lr_from_order` issues one LR per order -
    but the FK allows more, so this does not assume there is only one).

    Returns `(lr, before, after)`. Raises `ValueError` if no order is linked
    to this LR - there is nothing to sync freight from, and typing a figure
    in by hand is the only way to fill it, not a calculation bug.
    """
    orders = list(lr.orders.all())
    if not orders:
        raise ValueError(f"Lorry receipt {lr.number} has no order linked to it - nothing to sync freight from.")
    before = money(lr.freight_amount)
    after = money(sum((money(o.freight_amount) for o in orders), Decimal("0")))
    if after != before:
        lr.freight_amount = after
        if save:
            lr.save(update_fields=["freight_amount", "updated_at"])
    return lr, before, after
