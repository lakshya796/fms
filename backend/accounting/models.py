"""Double entry accounting for a transport business.

Every financial event - a freight invoice, a vendor bill, a diesel purchase, a
driver settlement - lands in a JournalEntry whose lines must balance. Reports
(trial balance, P&L, ageing, vehicle profitability) are then derived from those
lines rather than from module specific tables, which is what keeps the books
consistent once several people are entering data at once.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from iam.models import Branch


def money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


ACCOUNT_TYPES = [("asset", "Asset"), ("liability", "Liability"), ("equity", "Equity"),
                 ("income", "Income"), ("expense", "Expense")]
# Which side increases the balance. Assets and expenses are debit balances.
DEBIT_BALANCE_TYPES = ("asset", "expense")

VOUCHER_SOURCES = [("manual", "Manual journal"), ("invoice", "Customer invoice"), ("bill", "Vendor bill"),
                   ("receipt", "Customer receipt"), ("payment", "Vendor/driver payment"),
                   ("expense", "Trip expense"), ("fuel", "Fuel purchase"), ("settlement", "Driver settlement")]
PAYMENT_MODES = [("neft", "NEFT"), ("rtgs", "RTGS"), ("imps", "IMPS"), ("upi", "UPI"),
                 ("cheque", "Cheque"), ("cash", "Cash"), ("fastag", "FASTag wallet")]
CENTRE_TYPES = [("vehicle", "Vehicle"), ("branch", "Branch"), ("route", "Route"), ("driver", "Driver"), ("other", "Other")]


class FiscalYear(Timestamped):
    name = models.CharField(max_length=20, unique=True, help_text="e.g. 2026-27")
    start_date = models.DateField()
    end_date = models.DateField()
    is_closed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-start_date"]

    def contains(self, on_date):
        return self.start_date <= on_date <= self.end_date

    def __str__(self):
        return self.name


class Account(Timestamped):
    """A chart of accounts node."""
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=140)
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPES)
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children")
    is_bank = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    opening_balance = models.DecimalField(max_digits=16, decimal_places=2, default=0,
                                          help_text="Positive on the account's natural side")
    description = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["code"]

    @property
    def is_debit_balance(self):
        return self.account_type in DEBIT_BALANCE_TYPES

    @property
    def is_group(self):
        """A heading such as "Expenses". Postings belong on its leaves, never on it."""
        return self.children.exists()

    def balance(self, upto=None, branch=None):
        """Closing balance on the account's natural side."""
        lines = JournalLine.objects.filter(account=self, entry__is_posted=True)
        if upto:
            lines = lines.filter(entry__entry_date__lte=upto)
        if branch:
            lines = lines.filter(entry__branch=branch)
        totals = lines.aggregate(debit=models.Sum("debit"), credit=models.Sum("credit"))
        debit, credit = money(totals["debit"]), money(totals["credit"])
        movement = debit - credit if self.is_debit_balance else credit - debit
        return money(self.opening_balance + movement)

    def __str__(self):
        return f"{self.code} {self.name}"


class CostCentre(Timestamped):
    """Attaches a journal line to a truck, branch, route or driver so
    profitability can be sliced without a separate reporting pipeline."""
    name = models.CharField(max_length=140)
    code = models.CharField(max_length=30, unique=True)
    centre_type = models.CharField(max_length=20, choices=CENTRE_TYPES, default="vehicle")
    vehicle = models.ForeignKey("fleet.Vehicle", on_delete=models.SET_NULL, null=True, blank=True, related_name="cost_centres")
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="cost_centres")
    status = models.CharField(max_length=20, default="active")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class JournalEntry(Timestamped):
    number = models.CharField(max_length=30, unique=True)
    entry_date = models.DateField(default=timezone.localdate)
    narration = models.CharField(max_length=240, blank=True)
    source = models.CharField(max_length=20, choices=VOUCHER_SOURCES, default="manual")
    reference_type = models.CharField(max_length=40, blank=True)
    reference_id = models.CharField(max_length=40, blank=True)
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="journal_entries")
    fiscal_year = models.ForeignKey(FiscalYear, on_delete=models.SET_NULL, null=True, blank=True, related_name="entries")
    is_posted = models.BooleanField(default=True)
    reversed_by = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="reverses")
    created_by = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ["-entry_date", "-id"]
        indexes = [models.Index(fields=["-entry_date"]), models.Index(fields=["reference_type", "reference_id"])]
        verbose_name_plural = "journal entries"

    @property
    def total_debit(self):
        return money(self.lines.aggregate(value=models.Sum("debit"))["value"])

    @property
    def total_credit(self):
        return money(self.lines.aggregate(value=models.Sum("credit"))["value"])

    @property
    def is_balanced(self):
        return self.total_debit == self.total_credit

    def clean(self):
        if self.pk and not self.is_balanced:
            raise ValidationError("Debits and credits on a journal entry must be equal.")

    def __str__(self):
        return self.number


class JournalLine(Timestamped):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    debit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    cost_centre = models.ForeignKey(CostCentre, on_delete=models.SET_NULL, null=True, blank=True, related_name="lines")
    description = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["entry_id", "id"]
        indexes = [models.Index(fields=["account"]), models.Index(fields=["cost_centre"])]

    def clean(self):
        if self.debit and self.credit:
            raise ValidationError("A journal line carries either a debit or a credit, not both.")
        if not self.debit and not self.credit:
            raise ValidationError("A journal line needs a debit or a credit amount.")

    def __str__(self):
        return f"{self.account_id} Dr {self.debit} Cr {self.credit}"


class VendorBill(Timestamped):
    """Purchase invoice from an attached transporter, workshop or fuel vendor,
    with the TDS that Indian transporters deduct at source."""
    number = models.CharField(max_length=40, unique=True)
    vendor = models.ForeignKey("fleet.Vendor", on_delete=models.PROTECT, related_name="bills")
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="vendor_bills")
    bill_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, null=True, blank=True, related_name="vendor_bills")
    cost_centre = models.ForeignKey(CostCentre, on_delete=models.SET_NULL, null=True, blank=True, related_name="vendor_bills")
    taxable_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tds_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    narration = models.CharField(max_length=240, blank=True)
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name="vendor_bills")
    status = models.CharField(max_length=20, default="draft")

    class Meta:
        ordering = ["-bill_date", "-id"]

    def save(self, *args, **kwargs):
        if not self.total_amount:
            self.total_amount = money(self.taxable_amount + self.gst_amount - self.tds_amount)
        super().save(*args, **kwargs)

    @property
    def balance_due(self):
        return money(self.total_amount - self.paid_amount)

    def __str__(self):
        return self.number


class Payment(Timestamped):
    """Money out (payment) or money in (receipt), allocated against bills or invoices."""
    number = models.CharField(max_length=30, unique=True)
    payment_type = models.CharField(max_length=10, default="payment", choices=[("payment", "Payment"), ("receipt", "Receipt")])
    payment_date = models.DateField(default=timezone.localdate)
    customer = models.ForeignKey("fleet.Customer", on_delete=models.SET_NULL, null=True, blank=True, related_name="payments")
    vendor = models.ForeignKey("fleet.Vendor", on_delete=models.SET_NULL, null=True, blank=True, related_name="payments")
    driver = models.ForeignKey("fleet.Driver", on_delete=models.SET_NULL, null=True, blank=True, related_name="payments")
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments")
    bank_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="payments",
                                     help_text="Bank or cash account the money moved through")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    mode = models.CharField(max_length=20, choices=PAYMENT_MODES, default="neft")
    reference = models.CharField(max_length=60, blank=True, help_text="UTR, cheque or transaction reference")
    narration = models.CharField(max_length=240, blank=True)
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments")
    status = models.CharField(max_length=20, default="recorded")

    class Meta:
        ordering = ["-payment_date", "-id"]

    @property
    def allocated_amount(self):
        return money(self.allocations.aggregate(value=models.Sum("amount"))["value"])

    @property
    def unallocated_amount(self):
        return money(self.amount - self.allocated_amount)

    def __str__(self):
        return self.number


class PaymentAllocation(Timestamped):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey("fleet.Invoice", on_delete=models.CASCADE, null=True, blank=True, related_name="allocations")
    bill = models.ForeignKey(VendorBill, on_delete=models.CASCADE, null=True, blank=True, related_name="allocations")
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["payment_id", "id"]

    def clean(self):
        if bool(self.invoice_id) == bool(self.bill_id):
            raise ValidationError("Allocate against exactly one of an invoice or a vendor bill.")

    def __str__(self):
        return f"{self.payment_id} -> {self.amount}"
