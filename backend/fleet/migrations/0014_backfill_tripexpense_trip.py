"""Backfill `TripExpense.trip` from `order.trip` wherever it was left null. Before
this, an expense recorded with only `order` set (never happened from the trip
settlement upsert, but was always legal on the model) was invisible to
`Trip.settlement_summary`, which only ever aggregates `self.expenses` - i.e. rows
with `trip` set. `TripExpense.save()` now keeps the two in sync going forward; this
migration catches up whatever was written before that existed. See
docs/ONE-TRIP-END-TO-END.md §3.3/§5 Phase 2.
"""
from django.db import migrations


def backfill_trip_from_order(apps, schema_editor):
    TripExpense = apps.get_model("fleet", "TripExpense")
    orphaned = TripExpense.objects.filter(trip__isnull=True, order__trip__isnull=False).select_related("order")
    for expense in orphaned:
        expense.trip_id = expense.order.trip_id
        expense.save(update_fields=["trip"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("fleet", "0013_vendorlanerate"),
    ]

    operations = [
        migrations.RunPython(backfill_trip_from_order, noop),
    ]
