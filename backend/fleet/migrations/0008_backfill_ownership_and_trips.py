"""Two backfills that ride along with the Trip/Order unification and the ownership
enum: rename the old free-text "owned" value to the new "own" choice, and give every
already-assigned order a trip so its fuel and on-road cost stop being silently
excluded from profitability.
"""
from uuid import uuid4

from django.db import migrations
from django.utils import timezone


def rename_owned_to_own(apps, schema_editor):
    Vehicle = apps.get_model("fleet", "Vehicle")
    Vehicle.objects.filter(ownership="owned").update(ownership="own")


def backfill_order_trips(apps, schema_editor):
    Order = apps.get_model("fleet", "Order")
    Trip = apps.get_model("fleet", "Trip")
    orphaned = Order.objects.filter(trip__isnull=True, driver__isnull=False, vehicle__isnull=False) \
                            .exclude(status__in=["created", "cancelled"])
    for order in orphaned:
        trip = Trip.objects.create(
            number="TRP-" + timezone.now().strftime("%y%m%d") + uuid4().hex[:6].upper(),
            vehicle_id=order.vehicle_id, driver_id=order.driver_id,
            origin=order.pickup.city or order.pickup.name, destination=order.dropoff.city or order.dropoff.name,
            planned_departure=order.scheduled_at or order.created_at,
            actual_departure=order.dispatched_at, arrival_at=order.completed_at,
            status={"assigned": "planned", "dispatched": "dispatched", "in_transit": "dispatched",
                   "at_dropoff": "dispatched", "completed": "closed"}.get(order.status, "planned"))
        order.trip_id = trip.id
        order.save(update_fields=["trip"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("fleet", "0007_vehicle_contract_reference_vehicle_current_latitude_and_more"),
    ]

    operations = [
        migrations.RunPython(rename_owned_to_own, noop),
        migrations.RunPython(backfill_order_trips, noop),
    ]
