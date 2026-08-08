"""ADCOOP Voucher Portal - Phase 1.

An authenticated extension of the public gift voucher desk (see `vouchers/`), which
stays exactly as it is: public, fixed-value, phone-only issuing. This app is a
separate, richer model: percentage or fixed discounts, department/type-scoped
prefixes with server-allocated sequences, configurable artwork templates, and a
generated-vs-issued voucher lifecycle. It shares nothing with `vouchers` - no
shared tables, no shared foreign keys - so the two can run side by side without
either one's migrations touching the other's data.

Every batch snapshots the settings it was generated with (discount, validity,
terms, prefix width, template geometry) onto its own row. Editing a Department,
VoucherType, VoucherPrefix or VoucherTemplate afterwards must never change a
voucher that has already been printed and handed to someone.
"""
import copy
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .geometry import DEFAULT_FIELD_GEOMETRY


def default_field_geometry():
    """A fresh copy every time - a template created with no geometry of its own
    (e.g. uploading artwork with nothing else specified) still gets the coupon's
    known field positions, not an empty layout with nothing drawn on it."""
    return copy.deepcopy(DEFAULT_FIELD_GEOMETRY)


def money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def trim_decimal(value: Decimal) -> str:
    """"50" not "5E+1": Decimal.normalize() switches to scientific notation for
    round trailing-zero values, which is wrong for anything meant to be read."""
    return format(value.normalize(), "f")


class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Department(Timestamped):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class VoucherType(Timestamped):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="voucher_types")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class VoucherPrefix(Timestamped):
    """Owns one numbering sequence. `next_sequence` only ever advances, under a
    row lock (see `services/numbering.py`) - that lock is what makes concurrent
    batch generation safe against duplicate codes."""
    prefix = models.CharField(max_length=20, unique=True)
    label = models.CharField(max_length=120)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="prefixes")
    voucher_type = models.ForeignKey(VoucherType, on_delete=models.PROTECT, related_name="prefixes")
    sequence_length = models.PositiveSmallIntegerField(default=4)
    next_sequence = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["prefix"]

    def __str__(self):
        return f"{self.prefix} ({self.label})"


class VoucherTemplate(Timestamped):
    """One printable design. `field_geometry` positions every dynamic field in
    points from the coupon's top-left corner - see docs/VOUCHER-PORTAL.md for the
    default layout, measured from the approved ADCOOP coupon artwork."""
    name = models.CharField(max_length=120)
    artwork = models.ImageField(upload_to="voucher-portal/templates/", blank=True, null=True)
    page_width = models.FloatField(default=594.72, help_text="Points (A4 width)")
    page_height = models.FloatField(default=792.0, help_text="Points (Letter height)")
    coupon_width = models.FloatField(default=479.52)
    coupon_height = models.FloatField(default=178.0)
    field_geometry = models.JSONField(default=default_field_geometry, blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_default", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            VoucherTemplate.objects.exclude(pk=self.pk).update(is_default=False)


DISCOUNT_TYPES = [("percentage", "Percentage"), ("fixed", "Fixed amount")]

BATCH_STATUSES = [
    ("draft", "Draft"),
    ("generating", "Generating"),
    ("generated", "Generated"),
    ("failed", "Failed"),
]

VOUCHER_STATUSES = [
    ("generated", "Generated"),
    ("issued", "Issued"),
    ("redeemed", "Redeemed"),
    ("cancelled", "Cancelled"),
]


class PortalBatch(Timestamped):
    """A create-form submission, snapshotted. Every field a printed voucher
    depends on lives directly on this row - not behind a foreign key - so later
    edits to Department/VoucherType/VoucherPrefix/VoucherTemplate can never
    retroactively change a voucher already generated from this batch."""
    name = models.CharField(max_length=160)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="batches")
    voucher_type = models.ForeignKey(VoucherType, on_delete=models.PROTECT, related_name="batches")
    description = models.TextField(blank=True)
    quantity = models.PositiveIntegerField()

    discount_type = models.CharField(max_length=12, choices=DISCOUNT_TYPES)
    percentage_value = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    max_discount_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fixed_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=8, default="AED")

    valid_from = models.DateField()
    valid_to = models.DateField()

    restrictions = models.TextField(blank=True)
    terms = models.TextField(blank=True)

    prefix = models.ForeignKey(VoucherPrefix, on_delete=models.PROTECT, related_name="batches")
    prefix_snapshot = models.CharField(max_length=20, help_text="Copied from prefix.prefix at creation")
    sequence_length_snapshot = models.PositiveSmallIntegerField()

    template = models.ForeignKey(VoucherTemplate, on_delete=models.PROTECT, related_name="batches")
    template_snapshot = models.JSONField(default=dict, help_text="Copy of the template's geometry and artwork URL at creation")

    status = models.CharField(max_length=20, choices=BATCH_STATUSES, default="draft")
    combined_pdf_url = models.URLField(blank=True, help_text="Print-ready, all vouchers in one PDF")
    generation_error = models.TextField(blank=True)

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="voucher_batches")

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        if self.valid_to < self.valid_from:
            raise ValidationError("Validity end date is before the start date.")
        if self.discount_type == "percentage":
            if self.percentage_value is None or not (Decimal("0") < self.percentage_value <= Decimal("100")):
                raise ValidationError("Percentage value must be greater than 0 and no more than 100.")
        elif self.discount_type == "fixed":
            if self.fixed_value is None or self.fixed_value <= 0:
                raise ValidationError("Fixed voucher value must be greater than 0.")

    @property
    def display_value(self):
        if self.discount_type == "percentage":
            text = f"{trim_decimal(self.percentage_value)}% OFF"
            if self.max_discount_value:
                text += f" (up to {self.currency} {self.max_discount_value:,.2f})"
            return text
        return f"{self.currency} {self.fixed_value:,.2f} OFF"

    def __str__(self):
        return f"{self.name} ({self.prefix_snapshot})"


class PortalVoucher(Timestamped):
    batch = models.ForeignKey(PortalBatch, on_delete=models.CASCADE, related_name="vouchers")
    number = models.CharField(max_length=40, unique=True)
    status = models.CharField(max_length=20, choices=VOUCHER_STATUSES, default="generated")

    recipient_name = models.CharField(max_length=120, blank=True)
    recipient_phone = models.CharField(max_length=20, blank=True)
    recipient_email = models.EmailField(blank=True)
    recipient_reference = models.CharField(max_length=80, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    redeemed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    pdf_url = models.URLField(blank=True, help_text="Individual voucher PDF, for digital delivery")

    class Meta:
        ordering = ["number"]
        indexes = [models.Index(fields=["status"]), models.Index(fields=["recipient_phone"])]

    @property
    def is_expired(self):
        return self.batch.valid_to < timezone.localdate()

    @property
    def display_status(self):
        if self.status in ("redeemed", "cancelled"):
            return self.status
        if self.is_expired:
            return "expired"
        return self.status

    def issue(self, *, name="", phone="", email="", reference="", actor=None):
        self.recipient_name = name or ""
        self.recipient_phone = phone or ""
        self.recipient_email = email or ""
        self.recipient_reference = reference or ""
        self.status = "issued"
        self.issued_at = timezone.now()
        self.issued_by = actor
        self.save(update_fields=[
            "recipient_name", "recipient_phone", "recipient_email", "recipient_reference",
            "status", "issued_at", "issued_by", "updated_at",
        ])

    def __str__(self):
        return self.number


class StatusChange(Timestamped):
    """Append-only history for a batch or a voucher. Exactly one of `batch` /
    `voucher` is set."""
    batch = models.ForeignKey(PortalBatch, on_delete=models.CASCADE, null=True, blank=True, related_name="status_changes")
    voucher = models.ForeignKey(PortalVoucher, on_delete=models.CASCADE, null=True, blank=True, related_name="status_changes")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reason = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        target = self.batch_id and f"batch {self.batch_id}" or f"voucher {self.voucher_id}"
        return f"{target}: {self.from_status} -> {self.to_status}"
