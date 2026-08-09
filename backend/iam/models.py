"""Organisation structure, role based access and the audit trail.

A fleet running 1000+ vehicles is never a single desk: work is split across
branches, and dispatch, accounts and workshop staff each need a different slice
of the system. These models carry that structure.
"""
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# Permission catalogue. Roles hold a list of these strings; viewsets declare the
# one they need. Keeping it explicit (rather than Django's per-model permissions)
# keeps the console's role editor readable for an operations manager.
PERMISSION_CATALOGUE = [
    ("operations.view", "View orders, trips and tracking"),
    ("operations.manage", "Book, allocate, dispatch and close consignments"),
    ("masters.view", "View customers, vehicles, drivers and network masters"),
    ("masters.manage", "Create and edit masters"),
    ("rates.view", "View rate cards and quotations"),
    ("rates.manage", "Create and edit rate cards"),
    ("maintenance.view", "View work orders and service schedules"),
    ("maintenance.manage", "Raise and close work orders"),
    ("compliance.view", "View statutory documents"),
    ("compliance.manage", "Add and renew statutory documents"),
    ("expenses.view", "View fuel and trip expenses"),
    ("expenses.manage", "Record fuel and trip expenses"),
    ("expenses.approve", "Approve trip expenses and settlements"),
    ("accounting.view", "View ledgers, vouchers and financial reports"),
    ("accounting.manage", "Create invoices, bills, payments and journals"),
    ("accounting.post", "Post and reverse journal entries"),
    ("users.view", "View users, roles and branches"),
    ("users.manage", "Create users, assign roles and branches"),
    ("reports.view", "View dashboards and analytics"),
    ("messages.view", "View outbound emails and their delivery status"),
    ("messages.manage", "Resend outbound emails"),
]
PERMISSION_CODES = [code for code, _ in PERMISSION_CATALOGUE]

BRANCH_TYPES = [("head_office", "Head office"), ("branch", "Branch"), ("depot", "Depot"),
                ("warehouse", "Warehouse"), ("workshop", "Workshop")]


class Organisation(Timestamped):
    """The transport company itself. One row in practice, kept as a model so
    statutory identifiers live in the database rather than in settings."""
    name = models.CharField(max_length=180)
    legal_name = models.CharField(max_length=180, blank=True)
    gstin = models.CharField(max_length=15, blank=True)
    pan = models.CharField(max_length=10, blank=True)
    cin = models.CharField(max_length=21, blank=True)
    registered_address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=80, blank=True)
    pincode = models.CharField(max_length=6, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    fiscal_year_start_month = models.PositiveSmallIntegerField(default=4, help_text="April for the Indian financial year")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Branch(Timestamped):
    """A branch, depot or workshop. Most operational records carry one so a
    branch manager sees their own book of work."""
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, null=True, blank=True, related_name="branches")
    name = models.CharField(max_length=140)
    code = models.CharField(max_length=20, unique=True)
    branch_type = models.CharField(max_length=20, choices=BRANCH_TYPES, default="branch")
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80)
    state = models.CharField(max_length=80, blank=True)
    pincode = models.CharField(max_length=6, blank=True)
    gstin = models.CharField(max_length=15, blank=True, help_text="State wise GST registration for this branch")
    phone = models.CharField(max_length=20, blank=True)
    manager = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, default="active")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "branches"

    def __str__(self):
        return f"{self.name} ({self.code})"


class Role(Timestamped):
    """A named bundle of permissions, e.g. Dispatcher or Accounts executive."""
    name = models.CharField(max_length=80, unique=True)
    code = models.CharField(max_length=40, unique=True)
    description = models.CharField(max_length=240, blank=True)
    permissions = models.JSONField(default=list, blank=True)
    is_system = models.BooleanField(default=False, help_text="Seeded roles that should not be deleted")

    class Meta:
        ordering = ["name"]

    def allows(self, permission):
        return permission in (self.permissions or [])

    def __str__(self):
        return self.name


class UserProfile(Timestamped):
    """Everything about a login that Django's own User model does not carry."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    employee_code = models.CharField(max_length=30, unique=True)
    phone = models.CharField(max_length=20, blank=True)
    designation = models.CharField(max_length=120, blank=True)
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="staff")
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True, related_name="users")
    reporting_to = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="reportees")
    restrict_to_branch = models.BooleanField(default=False, help_text="Limit this user's records to their own branch")
    status = models.CharField(max_length=20, default="active")

    class Meta:
        ordering = ["user__username"]

    @property
    def permissions(self):
        if self.user.is_superuser:
            return PERMISSION_CODES
        return list(self.role.permissions or []) if self.role else []

    def allows(self, permission):
        return self.user.is_superuser or permission in self.permissions

    def __str__(self):
        return f"{self.user.username} ({self.employee_code})"


class AuditLog(Timestamped):
    """Who changed what. Written by the API layer, never edited by hand."""
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_entries")
    username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=20)
    entity = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=40, blank=True)
    summary = models.CharField(max_length=240, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        indexes = [models.Index(fields=["entity", "entity_id"]), models.Index(fields=["-recorded_at"])]

    def __str__(self):
        return f"{self.username} {self.action} {self.entity}"


OUTBOUND_CHANNELS = [("email", "Email"), ("sms", "SMS"), ("whatsapp", "WhatsApp")]
OUTBOUND_STATUSES = [("queued", "Queued"), ("sent", "Sent"), ("failed", "Failed")]


class OutboundMessage(Timestamped):
    """Every message the system sends out, recorded so it can be shown, audited and
    resent - vendor confirmations, and any future SMS/WhatsApp alert.

    Nothing in the fleet or accounting apps calls an email API directly; everything
    goes through here, which is what makes "resend" and "what did we actually send"
    possible without re-deriving it from application state.
    """
    channel = models.CharField(max_length=10, choices=OUTBOUND_CHANNELS, default="email")
    to_address = models.CharField(max_length=240)
    subject = models.CharField(max_length=240, blank=True)
    body = models.TextField(blank=True)
    template_key = models.CharField(max_length=60, blank=True)
    reference_type = models.CharField(max_length=40, blank=True)
    reference_id = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=10, choices=OUTBOUND_STATUSES, default="queued")
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=500, blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    created_by = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["reference_type", "reference_id"]), models.Index(fields=["status"])]

    def __str__(self):
        return f"{self.channel} to {self.to_address} ({self.status})"
