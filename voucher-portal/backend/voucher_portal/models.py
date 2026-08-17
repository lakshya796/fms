"""MAIR Voucher Portal - Phase 1.

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

from .geometry import BLANK_GEOMETRY


def default_field_geometry():
    """A fresh copy every time. A new template starts as an empty card carrying
    only the mandatory barcode - everything else is the designer's to add. It
    used to start with the fifteen measured coupon fields, which meant
    every design began by deleting someone else's layout."""
    return copy.deepcopy(BLANK_GEOMETRY)


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
    """One printable design. `field_geometry` is the layout document the
    designer edits: an ordered list of user-added elements positioned in points
    from the card's top-left corner (see voucher_portal/geometry.py). A new
    template holds only the mandatory barcode."""
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

    def clean(self):
        """Same rules the designer is held to.

        The API validates `field_geometry` in its serializer, which the Django
        admin doesn't go through - and the admin is exactly where someone hand-
        edits this document to repair it. Without this, a typo there is only
        discovered by a batch failing to print."""
        from .validators import GeometryError, validate_field_geometry

        try:
            validate_field_geometry(self.field_geometry, coupon_width=self.coupon_width,
                                    coupon_height=self.coupon_height)
        except GeometryError as error:
            raise ValidationError({"field_geometry": str(error)})

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            VoucherTemplate.objects.exclude(pk=self.pk).update(is_default=False)


VOUCHER_ROLES = [
    ("administrator", "Administrator"),
    ("requester", "Requester"),
    ("approver", "Approver"),
    ("report_viewer", "Report Viewer"),
]

# What each role may do. "download" covers both individual and combined PDFs;
# "admin" gates user/role/reference-data management. Deliberately coarse - one
# set of actions per role, not a per-department action matrix - department
# scope is handled separately by PortalUserAccess.departments.
ROLE_ACTIONS = {
    "administrator": frozenset({"create", "approve", "issue", "report", "admin", "download"}),
    "requester": frozenset({"create", "issue", "download"}),
    "approver": frozenset({"approve", "download", "report"}),
    "report_viewer": frozenset({"report"}),
}


class PortalUserAccess(Timestamped):
    """Voucher Portal membership for a login - deliberately separate from any
    fleet/iam role. Retail voucher staff and fleet operations staff are
    different people in a different org, even where they happen to share a
    Django login. A user with no row here, and who isn't Django staff or a
    superuser, has no access to the portal at all - Django staff/superusers
    get implicit Administrator access so the bootstrap admin login always
    works without a separate grant."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="voucher_access")
    role = models.CharField(max_length=20, choices=VOUCHER_ROLES, default="requester")
    departments = models.ManyToManyField(
        Department, blank=True, related_name="staff",
        help_text="A user can belong to any number of departments. Empty means every department - "
                  "only meaningful for Administrator or Report Viewer; Requester/Approver with no "
                  "department sees nothing.")
    can_view_others_vouchers = models.BooleanField(
        default=False,
        help_text="Off: this user only sees batches and vouchers they raised themselves. On: they see "
                  "everything in their departments. Roles that approve or report can always see across "
                  "users regardless - an approver who could only see their own requests would have "
                  "nothing to approve.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"


class Notification(Timestamped):
    """In-app only for Phase 2 - no SMTP is configured on this deployment yet.
    See docs/VOUCHER-PORTAL.md for wiring in real email later; nothing else
    about the approval workflow needs to change to add it."""
    NOTIFICATION_KINDS = [
        ("submitted", "Submitted for approval"),
        ("first_approved", "First approval granted"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="voucher_notifications")
    batch = models.ForeignKey("PortalBatch", on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=20, choices=NOTIFICATION_KINDS)
    message = models.CharField(max_length=240)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username}: {self.message}"


DISCOUNT_TYPES = [("percentage", "Percentage"), ("fixed", "Fixed amount")]

# Approval is two-stage: pending_approval is awaiting the first sign-off,
# pending_second_approval the second. "approved" means both are in and the
# batch can be generated, so nothing downstream of approval had to change.
BATCH_STATUSES = [
    ("draft", "Draft"),
    ("pending_approval", "Pending First Approval"),
    ("pending_second_approval", "Pending Second Approval"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("generating", "Generating"),
    ("generated", "Generated"),
    ("partially_issued", "Partially Issued"),
    ("fully_issued", "Fully Issued"),
    ("failed", "Failed"),
    ("cancelled", "Cancelled"),
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
    artwork = models.ImageField(
        upload_to="voucher-portal/batches/", blank=True, null=True,
        help_text="Optional batch-specific artwork that overrides the selected template artwork",
    )

    # 23 chars: "pending_second_approval" is the longest status value.
    status = models.CharField(max_length=24, choices=BATCH_STATUSES, default="draft")
    combined_pdf_url = models.URLField(blank=True, help_text="Print-ready, all vouchers in one PDF")
    generation_error = models.TextField(blank=True)

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="voucher_batches")
    # Two sign-offs. approved_by/approved_at keep their original meaning - the
    # final approval, the one that lets the batch generate - so reports, admin
    # and anything else reading them did not have to change when the first
    # stage was added in front.
    first_approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    first_approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=240, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

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

    def refresh_issue_status(self):
        """Batch status reflects how much of it has been issued, once it's
        generated. Called after every issue action rather than kept as a
        signal - the set of statuses this can move between is small and
        explicit at each call site."""
        if self.status not in ("generating", "generated", "partially_issued", "fully_issued"):
            return
        total = self.vouchers.count()
        issued = self.vouchers.filter(status="issued").count()
        if issued == 0:
            new_status = "generated"
        elif issued < total:
            new_status = "partially_issued"
        else:
            new_status = "fully_issued"
        if new_status != self.status:
            self.status = new_status
            self.save(update_fields=["status", "updated_at"])


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

    def redeem(self):
        self.status = "redeemed"
        self.redeemed_at = timezone.now()
        self.save(update_fields=["status", "redeemed_at", "updated_at"])

    def cancel(self):
        self.status = "cancelled"
        self.cancelled_at = timezone.now()
        self.save(update_fields=["status", "cancelled_at", "updated_at"])

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
