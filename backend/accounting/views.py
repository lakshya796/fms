from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from iam.filtering import apply_filters
from iam.permissions import HasModulePermission, requires
from iam.views import record_audit
from . import services
from .models import (Account, CostCentre, DEBIT_BALANCE_TYPES, FiscalYear, JournalEntry, JournalLine,
                     Payment, VendorBill, money)
from .serializers import (AccountSerializer, CostCentreSerializer, FiscalYearSerializer,
                          JournalEntrySerializer, PaymentSerializer, VendorBillSerializer)


class AccountingViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "accounting.view"
    required_write_permission = "accounting.manage"
    filter_fields: list = []
    search_fields: list = []

    def get_queryset(self):
        return apply_filters(super().get_queryset(), self.request.query_params,
                             self.filter_fields, self.search_fields, getattr(self, "date_field", None))


class FiscalYearViewSet(AccountingViewSet):
    queryset = FiscalYear.objects.all()
    serializer_class = FiscalYearSerializer
    search_fields = ["name"]


class AccountViewSet(AccountingViewSet):
    queryset = Account.objects.select_related("parent").all()
    serializer_class = AccountSerializer
    filter_fields = ["account_type", "is_bank", "is_active", "parent"]
    search_fields = ["code", "name"]


class CostCentreViewSet(AccountingViewSet):
    queryset = CostCentre.objects.select_related("vehicle", "branch").all()
    serializer_class = CostCentreSerializer
    filter_fields = ["centre_type", "status", "vehicle", "branch"]
    search_fields = ["name", "code"]


class JournalEntryViewSet(AccountingViewSet):
    queryset = JournalEntry.objects.select_related("branch").prefetch_related("lines__account", "lines__cost_centre").all()
    serializer_class = JournalEntrySerializer
    filter_fields = ["source", "branch", "is_posted", "reference_type"]
    search_fields = ["number", "narration"]
    date_field = "entry_date"

    def perform_create(self, serializer):
        entry = serializer.save(number=serializer.validated_data.get("number") or services.next_number("JV"),
                                created_by=self.request.user.username)
        record_audit(self.request, "create", "JournalEntry", entry.pk, entry.narration)

    @action(detail=True, methods=["post"], url_path="reverse")
    def reverse_voucher(self, request, pk=None):
        """Post the mirror entry rather than editing history."""
        entry = self.get_object()
        if not entry.is_posted:
            raise ValidationError("Only a posted entry can be reversed.")
        mirror = services.reverse_entry(entry, created_by=request.user.username)
        record_audit(request, "reverse", "JournalEntry", entry.pk, f"Reversed by {mirror.number}")
        return Response(self.get_serializer(mirror).data)


class VendorBillViewSet(AccountingViewSet):
    queryset = VendorBill.objects.select_related("vendor", "branch", "expense_account", "cost_centre").all()
    serializer_class = VendorBillSerializer
    filter_fields = ["vendor", "branch", "status"]
    search_fields = ["number", "narration"]
    date_field = "bill_date"

    @action(detail=True, methods=["post"])
    def post_to_ledger(self, request, pk=None):
        bill = self.get_object()
        try:
            entry = services.post_vendor_bill(bill, created_by=request.user.username)
        except services.PostingError as error:
            raise ValidationError(str(error))
        bill.journal_entry = entry
        bill.status = "posted"
        bill.save(update_fields=["journal_entry", "status", "updated_at"])
        record_audit(request, "post", "VendorBill", bill.pk, entry.number)
        return Response(self.get_serializer(bill).data)


class PaymentViewSet(AccountingViewSet):
    queryset = Payment.objects.select_related("customer", "vendor", "driver", "bank_account").prefetch_related("allocations").all()
    serializer_class = PaymentSerializer
    filter_fields = ["payment_type", "customer", "vendor", "driver", "branch", "mode", "status"]
    search_fields = ["number", "reference", "narration"]
    date_field = "payment_date"

    def perform_create(self, serializer):
        payment = serializer.save(number=serializer.validated_data.get("number") or services.next_number("PMT"))
        self._apply_allocations(payment)
        record_audit(self.request, "create", "Payment", payment.pk, payment.number)

    def _apply_allocations(self, payment):
        """Move the allocated amounts onto the invoices and bills themselves."""
        for allocation in payment.allocations.all():
            if allocation.bill_id:
                bill = allocation.bill
                bill.paid_amount = money(bill.paid_amount + allocation.amount)
                bill.status = "paid" if bill.paid_amount >= bill.total_amount else "part_paid"
                bill.save(update_fields=["paid_amount", "status", "updated_at"])
            elif allocation.invoice_id:
                invoice = allocation.invoice
                paid = money(invoice.allocations.aggregate(value=Sum("amount"))["value"])
                invoice.status = "paid" if paid >= invoice.total_amount else "part_paid"
                invoice.save(update_fields=["status", "updated_at"])

    @action(detail=True, methods=["post"])
    def post_to_ledger(self, request, pk=None):
        payment = self.get_object()
        try:
            entry = services.post_payment(payment, created_by=request.user.username)
        except services.PostingError as error:
            raise ValidationError(str(error))
        payment.journal_entry = entry
        payment.status = "posted"
        payment.save(update_fields=["journal_entry", "status", "updated_at"])
        record_audit(request, "post", "Payment", payment.pk, entry.number)
        return Response(self.get_serializer(payment).data)


# --- reports ---------------------------------------------------------------

def _period(request):
    today = timezone.localdate()
    start = request.query_params.get("from") or str(today.replace(month=4, day=1) if today.month >= 4
                                                    else today.replace(year=today.year - 1, month=4, day=1))
    end = request.query_params.get("to") or str(today)
    return start, end


def _posted_lines(start=None, end=None, branch=None):
    lines = JournalLine.objects.filter(entry__is_posted=True).select_related("account", "entry")
    if start:
        lines = lines.filter(entry__entry_date__gte=start)
    if end:
        lines = lines.filter(entry__entry_date__lte=end)
    if branch:
        lines = lines.filter(entry__branch_id=branch)
    return lines


@requires("accounting.view")
@api_view(["GET"])
def trial_balance(request):
    """Every account with movement in the period, and the balancing check."""
    start, end = _period(request)
    branch = request.query_params.get("branch")
    rows = (_posted_lines(start, end, branch)
            .values("account__code", "account__name", "account__account_type")
            .annotate(debit=Sum("debit"), credit=Sum("credit")).order_by("account__code"))
    result, total_debit, total_credit = [], Decimal("0"), Decimal("0")
    for row in rows:
        debit, credit = money(row["debit"]), money(row["credit"])
        total_debit += debit
        total_credit += credit
        natural_debit = row["account__account_type"] in DEBIT_BALANCE_TYPES
        balance = debit - credit if natural_debit else credit - debit
        result.append({"code": row["account__code"], "name": row["account__name"],
                       "account_type": row["account__account_type"], "debit": float(debit),
                       "credit": float(credit), "balance": float(money(balance))})
    return Response({"from": start, "to": end, "accounts": result,
                     "total_debit": float(money(total_debit)), "total_credit": float(money(total_credit)),
                     "balanced": money(total_debit) == money(total_credit)})


@requires("accounting.view")
@api_view(["GET"])
def profit_and_loss(request):
    """Income less expenses for the period, with the lines that make it up."""
    start, end = _period(request)
    branch = request.query_params.get("branch")
    lines = _posted_lines(start, end, branch).filter(account__account_type__in=["income", "expense"])
    rows = (lines.values("account__code", "account__name", "account__account_type")
            .annotate(debit=Sum("debit"), credit=Sum("credit")).order_by("account__code"))
    income, expense = [], []
    total_income, total_expense = Decimal("0"), Decimal("0")
    for row in rows:
        debit, credit = money(row["debit"]), money(row["credit"])
        if row["account__account_type"] == "income":
            amount = credit - debit
            total_income += amount
            income.append({"code": row["account__code"], "name": row["account__name"], "amount": float(money(amount))})
        else:
            amount = debit - credit
            total_expense += amount
            expense.append({"code": row["account__code"], "name": row["account__name"], "amount": float(money(amount))})
    profit = money(total_income - total_expense)
    margin = float(round(profit / total_income * 100, 2)) if total_income else 0.0
    return Response({"from": start, "to": end, "income": income, "expenses": expense,
                     "total_income": float(money(total_income)), "total_expense": float(money(total_expense)),
                     "net_profit": float(profit), "margin_percent": margin})


@requires("accounting.view")
@api_view(["GET"])
def account_ledger(request):
    """Running balance for one account, the view an accountant actually wants."""
    code = request.query_params.get("account")
    if not code:
        raise ValidationError("Provide `account` as an account code, e.g. account=1200.")
    try:
        ledger_account = Account.objects.get(code=code)
    except Account.DoesNotExist:
        raise ValidationError(f"No account with code {code}.")
    start, end = _period(request)
    opening = ledger_account.balance(upto=start)
    running = opening
    entries = []
    for line in (_posted_lines(start, end).filter(account=ledger_account)
                 .order_by("entry__entry_date", "entry_id", "id")):
        movement = line.debit - line.credit if ledger_account.is_debit_balance else line.credit - line.debit
        running = money(running + movement)
        entries.append({"date": str(line.entry.entry_date), "voucher": line.entry.number,
                        "source": line.entry.source, "narration": line.entry.narration,
                        "description": line.description, "debit": float(money(line.debit)),
                        "credit": float(money(line.credit)), "balance": float(running)})
    return Response({"account": {"code": ledger_account.code, "name": ledger_account.name,
                                 "type": ledger_account.account_type},
                     "from": start, "to": end, "opening_balance": float(money(opening)),
                     "closing_balance": float(running), "entries": entries})


def _ageing(rows, today):
    """Bucket outstanding documents the way a collections desk reads them."""
    buckets = defaultdict(lambda: {"current": Decimal("0"), "1_30": Decimal("0"), "31_60": Decimal("0"),
                                   "61_90": Decimal("0"), "over_90": Decimal("0"), "total": Decimal("0")})
    for party, due_date, outstanding in rows:
        if outstanding <= 0:
            continue
        overdue_days = (today - due_date).days if due_date else 0
        key = ("current" if overdue_days <= 0 else "1_30" if overdue_days <= 30 else
               "31_60" if overdue_days <= 60 else "61_90" if overdue_days <= 90 else "over_90")
        buckets[party][key] += outstanding
        buckets[party]["total"] += outstanding
    return [{"party": party, **{bucket: float(money(value)) for bucket, value in totals.items()}}
            for party, totals in sorted(buckets.items(), key=lambda item: -item[1]["total"])]


@requires("accounting.view")
@api_view(["GET"])
def receivable_ageing(request):
    """Customer outstanding by age. Unpaid invoice value less what has been allocated."""
    from fleet.models import Invoice
    today = timezone.localdate()
    rows = []
    for invoice in (Invoice.objects.exclude(status="paid").select_related("customer")
                    .annotate(received=Sum("allocations__amount"))):
        outstanding = money(invoice.total_amount) - money(invoice.received or 0)
        rows.append((invoice.customer.name, invoice.due_date, outstanding))
    buckets = _ageing(rows, today)
    return Response({"as_on": str(today), "parties": buckets,
                     "total_outstanding": float(money(sum(Decimal(str(row["total"])) for row in buckets)))})


@requires("accounting.view")
@api_view(["GET"])
def payable_ageing(request):
    today = timezone.localdate()
    rows = [(bill.vendor.name, bill.due_date or bill.bill_date, bill.balance_due)
            for bill in VendorBill.objects.exclude(status="paid").select_related("vendor")]
    buckets = _ageing(rows, today)
    return Response({"as_on": str(today), "parties": buckets,
                     "total_outstanding": float(money(sum(Decimal(str(row["total"])) for row in buckets)))})


@requires("accounting.view")
@api_view(["GET"])
def vehicle_profitability(request):
    """Revenue less running cost per truck - the number a fleet owner lives by."""
    from fleet.models import FuelEntry, Order, TripExpense
    start, end = _period(request)
    revenue = defaultdict(Decimal)
    for row in (Order.objects.filter(status="completed", completed_at__date__gte=start, completed_at__date__lte=end)
                .exclude(vehicle=None).values("vehicle__registration_number").annotate(value=Sum("total_amount"))):
        revenue[row["vehicle__registration_number"]] += money(row["value"])
    cost = defaultdict(Decimal)
    for row in (FuelEntry.objects.filter(entry_date__gte=start, entry_date__lte=end)
                .values("vehicle__registration_number").annotate(value=Sum("amount"))):
        cost[row["vehicle__registration_number"]] += money(row["value"])
    for row in (TripExpense.objects.filter(expense_date__gte=start, expense_date__lte=end)
                .exclude(vehicle=None).values("vehicle__registration_number").annotate(value=Sum("amount"))):
        cost[row["vehicle__registration_number"]] += money(row["value"])
    vehicles = sorted(set(revenue) | set(cost))
    rows = []
    for registration in vehicles:
        earned, spent = money(revenue.get(registration, 0)), money(cost.get(registration, 0))
        profit = money(earned - spent)
        rows.append({"vehicle": registration, "revenue": float(earned), "cost": float(spent),
                     "profit": float(profit),
                     "margin_percent": float(round(profit / earned * 100, 2)) if earned else 0.0})
    rows.sort(key=lambda row: -row["profit"])
    return Response({"from": start, "to": end, "vehicles": rows,
                     "total_revenue": float(money(sum(Decimal(str(r["revenue"])) for r in rows))),
                     "total_cost": float(money(sum(Decimal(str(r["cost"])) for r in rows))),
                     "total_profit": float(money(sum(Decimal(str(r["profit"])) for r in rows)))})


@requires("accounting.view")
@api_view(["GET"])
def gst_summary(request):
    """Output and input GST for the period, the starting point for GSTR filing."""
    start, end = _period(request)
    lines = _posted_lines(start, end)
    output = lines.filter(account__code="2200").aggregate(d=Sum("debit"), c=Sum("credit"))
    inputs = lines.filter(account__code="1300").aggregate(d=Sum("debit"), c=Sum("credit"))
    output_tax = money(output["c"]) - money(output["d"])
    input_tax = money(inputs["d"]) - money(inputs["c"])
    return Response({"from": start, "to": end, "output_gst": float(output_tax), "input_gst": float(input_tax),
                     "net_payable": float(money(output_tax - input_tax))})
