"""Give the proofs that already exist a sensible place in the new ePOD workflow.

Before this, a `ProofOfDelivery` was a single row written when the order was
completed, with `captured_at` defaulting to now. The new `status` column defaults
to "awaiting", which would leave every one of those already-delivered
consignments looking like a truck still on its way — and, worse, would block them
from being invoiced.

So: a capture that already happened is settled the same way a fresh one is. Clean
ones are verified; ones carrying a shortage or damage go to the office queue,
which is where they belong anyway.
"""
from django.db import migrations


def settle_existing(apps, schema_editor):
    ProofOfDelivery = apps.get_model("fleet", "ProofOfDelivery")
    captured = ProofOfDelivery.objects.filter(captured_at__isnull=False, status="awaiting")
    captured.filter(damage_reported=False, shortage_kg=0).update(
        status="verified", verified_by="Recorded before ePOD review existed")
    captured.exclude(damage_reported=False, shortage_kg=0).update(status="submitted")


def unsettle(apps, schema_editor):
    ProofOfDelivery = apps.get_model("fleet", "ProofOfDelivery")
    ProofOfDelivery.objects.filter(verified_by="Recorded before ePOD review existed").update(
        status="awaiting", verified_by="")


class Migration(migrations.Migration):

    dependencies = [("fleet", "0004_epod_and_auto_freight")]

    operations = [migrations.RunPython(settle_existing, unsettle)]
