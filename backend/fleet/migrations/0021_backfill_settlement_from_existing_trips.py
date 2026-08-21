"""Backfill `Settlement` rows for trips that already had expense/advance data on
file before `TripViewSet.settlement` started syncing the driver settlement ledger
on every save. Without this, only a trip settlement saved *after* that change
gets a Settlement row - every trip settled earlier stays invisible to the
Settlements module and to reports that read from `Settlement` (report_vehicle_settlement,
the dashboard's pending_settlements figure), even though its expenses and advance
are already sitting on the Trip/TripExpense rows.

A trip with neither expenses nor an advance has nothing worth settling, so it's
skipped rather than creating an all-zero row. A trip that already has a Settlement
for its driver (created by hand, or by a settlement save after the sync landed) is
left alone.
"""
from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


def backfill_settlements(apps, schema_editor):
    Trip = apps.get_model("fleet", "Trip")
    Settlement = apps.get_model("fleet", "Settlement")
    existing = set(Settlement.objects.values_list("trip_id", "driver_id"))
    for trip in Trip.objects.all().iterator():
        if (trip.id, trip.driver_id) in existing:
            continue
        total_exp = trip.expenses.aggregate(value=Sum("amount"))["value"] or Decimal("0")
        if not total_exp and not trip.advance_amount:
            continue
        Settlement.objects.create(
            trip_id=trip.id, driver_id=trip.driver_id,
            advance_amount=trip.advance_amount, approved_expenses=total_exp,
            net_payable=total_exp - trip.advance_amount, status="pending",
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("fleet", "0020_order_freight_source_servicerate_fixed_charge_scope"),
    ]

    operations = [
        migrations.RunPython(backfill_settlements, noop),
    ]
