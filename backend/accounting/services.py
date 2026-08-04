"""Posting rules that turn operational events into balanced journal entries.

Every helper here is idempotent per source document: posting the same invoice
twice returns the existing entry rather than double counting revenue.
"""
from decimal import Decimal
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from .models import Account, JournalEntry, JournalLine, money

# Codes the posting rules rely on. seed_accounting creates them.
RECEIVABLE = "1200"
PAYABLE = "2100"
INPUT_GST = "1300"
OUTPUT_GST = "2200"
TDS_PAYABLE = "2300"
DRIVER_ADVANCE = "1400"
CASH = "1110"
FREIGHT_INCOME = "4100"
HIRED_FREIGHT = "5600"
EXPENSE_ACCOUNT_BY_CATEGORY = {
    "diesel": "5100", "toll": "5200", "driver_allowance": "5300", "halting": "5300",
    "repair": "5400", "loading": "5700", "unloading": "5700",
    "rto_fine": "5800", "police": "5800", "permit": "5800", "parking": "5800", "other": "5900",
}


class PostingError(Exception):
    """Raised when the chart of accounts is missing something a rule needs."""


def account(code):
    try:
        return Account.objects.get(code=code)
    except Account.DoesNotExist:
        raise PostingError(f"Account {code} is missing. Run `python manage.py seed_accounting` first.")


def next_number(prefix):
    """Unique even when a batch of vouchers posts in the same millisecond."""
    return f"{prefix}-{timezone.now().strftime('%y%m%d')}-{uuid4().hex[:8].upper()}"


def existing_entry(reference_type, reference_id):
    return JournalEntry.objects.filter(reference_type=reference_type, reference_id=str(reference_id)).first()


@transaction.atomic
def create_entry(*, source, narration, lines, entry_date=None, branch=None,
                 reference_type="", reference_id="", created_by=""):
    """Create a balanced entry. `lines` is a list of (account, debit, credit, cost_centre, description)."""
    total_debit = money(sum(Decimal(str(line[1] or 0)) for line in lines))
    total_credit = money(sum(Decimal(str(line[2] or 0)) for line in lines))
    if total_debit != total_credit:
        raise PostingError(f"Entry does not balance: debits {total_debit} vs credits {total_credit}")
    if not total_debit:
        raise PostingError("Refusing to post an entry with no value.")
    for account_obj, _debit, _credit, _centre, _description in lines:
        if account_obj.is_group:
            raise PostingError(f"{account_obj.code} {account_obj.name} is a group heading; post to a sub-account.")
    entry = JournalEntry.objects.create(
        number=next_number("JV"), source=source, narration=narration[:240],
        entry_date=entry_date or timezone.localdate(), branch=branch,
        reference_type=reference_type, reference_id=str(reference_id), created_by=created_by)
    for account_obj, debit, credit, cost_centre, description in lines:
        if not debit and not credit:
            continue
        JournalLine.objects.create(entry=entry, account=account_obj, debit=money(debit), credit=money(credit),
                                   cost_centre=cost_centre, description=(description or "")[:240])
    return entry


def post_customer_invoice(invoice, branch=None, created_by=""):
    """Dr Debtors, Cr Freight income, Cr Output GST."""
    found = existing_entry("invoice", invoice.pk)
    if found:
        return found
    freight = money(invoice.freight_amount) + money(invoice.additional_charges)
    tax = money(invoice.tax_amount)
    return create_entry(
        source="invoice", narration=f"Invoice {invoice.number} to {invoice.customer.name}",
        entry_date=invoice.created_at.date() if invoice.created_at else None, branch=branch,
        reference_type="invoice", reference_id=invoice.pk, created_by=created_by,
        lines=[
            (account(RECEIVABLE), money(invoice.total_amount), 0, None, invoice.customer.name),
            (account(FREIGHT_INCOME), 0, freight, None, f"Freight {invoice.number}"),
            (account(OUTPUT_GST), 0, tax, None, "Output GST"),
        ])


def post_vendor_bill(bill, created_by=""):
    """Dr Expense, Dr Input GST, Cr Creditors, Cr TDS payable."""
    found = existing_entry("bill", bill.pk)
    if found:
        return found
    expense = bill.expense_account or account(HIRED_FREIGHT)
    return create_entry(
        source="bill", narration=f"Bill {bill.number} from {bill.vendor.name}",
        entry_date=bill.bill_date, branch=bill.branch,
        reference_type="bill", reference_id=bill.pk, created_by=created_by,
        lines=[
            (expense, money(bill.taxable_amount), 0, bill.cost_centre, bill.narration or bill.vendor.name),
            (account(INPUT_GST), money(bill.gst_amount), 0, None, "Input GST"),
            (account(PAYABLE), 0, money(bill.total_amount), None, bill.vendor.name),
            (account(TDS_PAYABLE), 0, money(bill.tds_amount), None, "TDS deducted at source"),
        ])


def post_payment(payment, created_by=""):
    """Receipt: Dr Bank, Cr Debtors. Payment: Dr Creditors (or driver advance), Cr Bank."""
    found = existing_entry("payment", payment.pk)
    if found:
        return found
    bank = payment.bank_account or account(CASH)
    amount = money(payment.amount)
    party = payment.customer or payment.vendor or payment.driver
    label = getattr(party, "name", "") or payment.narration or payment.number
    if payment.payment_type == "receipt":
        lines = [(bank, amount, 0, None, label), (account(RECEIVABLE), 0, amount, None, label)]
    else:
        contra = account(DRIVER_ADVANCE) if payment.driver_id and not payment.vendor_id else account(PAYABLE)
        lines = [(contra, amount, 0, None, label), (bank, 0, amount, None, label)]
    return create_entry(
        source="receipt" if payment.payment_type == "receipt" else "payment",
        narration=f"{payment.get_payment_type_display()} {payment.number} - {label}",
        entry_date=payment.payment_date, branch=payment.branch,
        reference_type="payment", reference_id=payment.pk, created_by=created_by, lines=lines)


def post_trip_expense(expense, cost_centre=None, created_by=""):
    """Dr the matching expense head, Cr driver advance when the driver paid, else cash."""
    found = existing_entry("expense", expense.pk)
    if found:
        return found
    head = account(EXPENSE_ACCOUNT_BY_CATEGORY.get(expense.category, "5900"))
    contra = account(DRIVER_ADVANCE) if expense.paid_by == "driver" else account(CASH)
    amount = money(expense.amount)
    return create_entry(
        source="expense", narration=f"{expense.get_category_display()} {expense.receipt_number or ''}".strip(),
        entry_date=expense.expense_date, reference_type="expense", reference_id=expense.pk, created_by=created_by,
        lines=[(head, amount, 0, cost_centre, expense.remarks or expense.category),
               (contra, 0, amount, None, expense.paid_by)])


def post_fuel_entry(fuel, cost_centre=None, created_by=""):
    """Dr Diesel, Cr the wallet the fuel was bought on."""
    found = existing_entry("fuel", fuel.pk)
    if found:
        return found
    amount = money(fuel.amount)
    contra = account(DRIVER_ADVANCE) if fuel.payment_method == "cash" else account(PAYABLE)
    return create_entry(
        source="fuel", narration=f"Diesel {fuel.volume_litres}L {fuel.station_name}".strip(),
        entry_date=fuel.entry_date, reference_type="fuel", reference_id=fuel.pk, created_by=created_by,
        lines=[(account("5100"), amount, 0, cost_centre, fuel.vehicle.registration_number),
               (contra, 0, amount, None, fuel.get_payment_method_display())])


@transaction.atomic
def reverse_entry(entry, created_by=""):
    """Post the mirror image of an entry and link the two."""
    if entry.reversed_by_id:
        return entry.reversed_by
    mirror = create_entry(
        source=entry.source, narration=f"Reversal of {entry.number}", entry_date=timezone.localdate(),
        branch=entry.branch, reference_type="reversal", reference_id=entry.pk, created_by=created_by,
        lines=[(line.account, line.credit, line.debit, line.cost_centre, f"Reversal: {line.description}")
               for line in entry.lines.all()])
    entry.reversed_by = mirror
    entry.save(update_fields=["reversed_by", "updated_at"])
    return mirror
