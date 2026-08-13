"""MAIR gift voucher batches.

This is a standalone module - a retail voucher desk, not part of the fleet/transport
domain the rest of this project models. It shares the same Django project purely for
deployment convenience; nothing here references fleet, iam or accounting.

The page in front of this is deliberately public (no login), so the one thing this
layer must defend on its own is scale: a batch is capped (see `MAX_BATCH_SIZE`) so a
single request can't mint an unbounded number of monetary vouchers.
"""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import models
from django.utils import timezone


def money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


VOUCHER_STATUSES = [("unassigned", "Unassigned"), ("issued", "Issued")]

# A public, unauthenticated endpoint that mints monetary vouchers needs a hard
# ceiling regardless of what the caller asks for.
MAX_BATCH_SIZE = 1000


class VoucherBatch(Timestamped):
    """One print run: a numbered series, a denomination, and a validity window."""
    series_prefix = models.CharField(max_length=40)
    padding = models.PositiveSmallIntegerField(default=6, help_text="Zero-padded width of the numeric part")
    start_number = models.PositiveIntegerField()
    end_number = models.PositiveIntegerField()
    value = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=8, default="AED")
    valid_from = models.DateField()
    valid_to = models.DateField()
    created_by = models.CharField(max_length=120, blank=True, help_text="Whoever filled in the form - free text, there is no login")

    class Meta:
        ordering = ["-created_at"]

    @property
    def count(self):
        return self.end_number - self.start_number + 1

    @property
    def first_number(self):
        return f"{self.series_prefix}{self.start_number:0{self.padding}d}"

    @property
    def last_number(self):
        return f"{self.series_prefix}{self.end_number:0{self.padding}d}"

    def __str__(self):
        return f"{self.first_number} - {self.last_number}"


class GiftVoucher(Timestamped):
    """A single voucher. Its number is the human-readable identifier and what the barcode encodes."""
    batch = models.ForeignKey(VoucherBatch, on_delete=models.CASCADE, related_name="vouchers")
    number = models.CharField(max_length=60, unique=True)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=8, default="AED")
    valid_from = models.DateField()
    valid_to = models.DateField()
    phone_number = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=20, choices=VOUCHER_STATUSES, default="unassigned")
    issued_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["number"]
        indexes = [models.Index(fields=["status"]), models.Index(fields=["phone_number"])]

    @property
    def is_expired(self):
        return self.valid_to < timezone.localdate()

    @property
    def display_status(self):
        if self.is_expired:
            return "expired"
        return self.status

    def issue(self, phone_number=""):
        self.phone_number = (phone_number or "").strip()
        self.status = "issued"
        self.issued_at = timezone.now()
        self.save(update_fields=["phone_number", "status", "issued_at", "updated_at"])

    def __str__(self):
        return self.number
