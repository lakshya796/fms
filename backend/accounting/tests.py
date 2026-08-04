"""Tests for the double entry ledger, vouchers and financial reports."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from fleet.models import Customer, Driver, FuelEntry, Invoice, Order, Place, ServiceArea, ServiceRate, Trip, TripExpense, Vehicle, Vendor
from . import services
from .models import Account, CostCentre, JournalEntry, Payment, PaymentAllocation, VendorBill


class AccountingTestCase(TestCase):
    def setUp(self):
        call_command("seed_accounting", verbosity=0)
        self.user = User.objects.create_superuser("accountant", "a@example.com", "ledger-desk-pass-2026")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.customer = Customer.objects.create(name="Tata Consumer", gstin="27AAACT2727Q1ZW")
        self.vendor = Vendor.objects.create(name="Shree Balaji Roadlines", code="VN-001", tds_percent=2)
        self.vehicle = Vehicle.objects.create(registration_number="MH 04 JU 9182", vehicle_type="32 ft", capacity_kg=16000)
        self.driver = Driver.objects.create(name="Ramesh", phone="+919820011223", licence_number="MH03X")
        self.bank = Account.objects.get(code="1120")

    def make_invoice(self, freight=100000, tax=5000, **kwargs):
        trip = Trip.objects.create(number=f"TRP-{Trip.objects.count() + 1}", vehicle=self.vehicle, driver=self.driver,
                                   origin="Bhiwandi", destination="Chakan", planned_departure=timezone.now())
        return Invoice.objects.create(
            number=f"INV-{Invoice.objects.count() + 1:04d}", customer=self.customer, trip=trip,
            freight_amount=freight, additional_charges=0, tax_amount=tax,
            total_amount=freight + tax, due_date=kwargs.get("due_date", timezone.localdate() + timedelta(days=30)),
            status=kwargs.get("status", "issued"))


class ChartOfAccountsTests(AccountingTestCase):
    def test_seed_creates_the_indian_transport_chart(self):
        self.assertEqual(Account.objects.count(), 29)
        self.assertEqual(Account.objects.get(code="4100").name, "Freight income")
        self.assertEqual(Account.objects.get(code="2300").account_type, "liability")
        self.assertTrue(Account.objects.get(code="1120").is_bank)

    def test_seed_is_idempotent(self):
        call_command("seed_accounting", verbosity=0)
        self.assertEqual(Account.objects.count(), 29)

    def test_account_balance_follows_its_natural_side(self):
        services.create_entry(source="manual", narration="Opening",
                              lines=[(self.bank, 50000, 0, None, "in"),
                                     (Account.objects.get(code="3100"), 0, 50000, None, "capital")])
        self.assertEqual(self.bank.balance(), Decimal("50000.00"))
        self.assertEqual(Account.objects.get(code="3100").balance(), Decimal("50000.00"))


class JournalEntryTests(AccountingTestCase):
    def test_an_unbalanced_entry_is_rejected(self):
        response = self.client.post("/api/v1/accounting/journal-entries/", {
            "narration": "Wrong", "number": "JV-BAD",
            "lines": [{"account": self.bank.id, "debit": "500", "credit": "0"},
                      {"account": Account.objects.get(code="4100").id, "debit": "0", "credit": "400"}]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("balance", str(response.data).lower())

    def test_a_line_cannot_be_both_debit_and_credit(self):
        response = self.client.post("/api/v1/accounting/journal-entries/", {
            "narration": "Both sides", "lines": [
                {"account": self.bank.id, "debit": "500", "credit": "500"},
                {"account": Account.objects.get(code="4100").id, "debit": "0", "credit": "0"}]}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_a_balanced_entry_posts_and_numbers_itself(self):
        response = self.client.post("/api/v1/accounting/journal-entries/", {
            "narration": "Capital introduced", "lines": [
                {"account": self.bank.id, "debit": "250000", "credit": "0"},
                {"account": Account.objects.get(code="3100").id, "debit": "0", "credit": "250000"}]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["number"].startswith("JV-"))
        self.assertTrue(response.data["is_balanced"])

    def test_reversal_mirrors_the_original_and_nets_to_zero(self):
        entry = services.create_entry(source="manual", narration="To reverse",
                                      lines=[(self.bank, 10000, 0, None, ""),
                                             (Account.objects.get(code="4100"), 0, 10000, None, "")])
        response = self.client.post(f"/api/v1/accounting/journal-entries/{entry.id}/reverse/")
        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.assertIsNotNone(entry.reversed_by)
        self.assertEqual(self.bank.balance(), Decimal("0.00"))


class PostingRuleTests(AccountingTestCase):
    def test_invoice_posts_receivable_income_and_output_gst(self):
        invoice = self.make_invoice(freight=100000, tax=5000)
        entry = services.post_customer_invoice(invoice)
        self.assertTrue(entry.is_balanced)
        self.assertEqual(Account.objects.get(code="1200").balance(), Decimal("105000.00"))
        self.assertEqual(Account.objects.get(code="4100").balance(), Decimal("100000.00"))
        self.assertEqual(Account.objects.get(code="2200").balance(), Decimal("5000.00"))

    def test_posting_the_same_invoice_twice_does_not_double_the_revenue(self):
        invoice = self.make_invoice(freight=100000, tax=5000)
        first = services.post_customer_invoice(invoice)
        second = services.post_customer_invoice(invoice)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Account.objects.get(code="4100").balance(), Decimal("100000.00"))

    def test_vendor_bill_posts_expense_input_gst_creditor_and_tds(self):
        bill = VendorBill.objects.create(number="BILL-1", vendor=self.vendor, taxable_amount=50000,
                                         gst_amount=2500, tds_amount=1000)
        entry = services.post_vendor_bill(bill)
        self.assertTrue(entry.is_balanced)
        self.assertEqual(bill.total_amount, Decimal("51500.00"))          # 50000 + 2500 - 1000
        self.assertEqual(Account.objects.get(code="5600").balance(), Decimal("50000.00"))
        self.assertEqual(Account.objects.get(code="1300").balance(), Decimal("2500.00"))
        self.assertEqual(Account.objects.get(code="2300").balance(), Decimal("1000.00"))

    def test_receipt_clears_the_receivable(self):
        invoice = self.make_invoice(freight=100000, tax=5000)
        services.post_customer_invoice(invoice)
        payment = Payment.objects.create(number="RCT-1", payment_type="receipt", customer=self.customer,
                                         bank_account=self.bank, amount=105000, mode="neft")
        services.post_payment(payment)
        self.assertEqual(Account.objects.get(code="1200").balance(), Decimal("0.00"))
        self.assertEqual(self.bank.balance(), Decimal("105000.00"))

    def test_trip_expense_paid_by_driver_hits_the_driver_advance(self):
        expense = TripExpense.objects.create(vehicle=self.vehicle, driver=self.driver, category="toll",
                                             amount=3200, paid_by="driver")
        entry = services.post_trip_expense(expense)
        self.assertTrue(entry.is_balanced)
        self.assertEqual(Account.objects.get(code="5200").balance(), Decimal("3200.00"))
        self.assertEqual(Account.objects.get(code="1400").balance(), Decimal("-3200.00"))

    def test_posting_without_a_chart_of_accounts_explains_itself(self):
        Account.objects.all().delete()
        with self.assertRaises(services.PostingError) as caught:
            services.post_customer_invoice(self.make_invoice())
        self.assertIn("seed_accounting", str(caught.exception))


class ReportTests(AccountingTestCase):
    def setUp(self):
        super().setUp()
        invoice = self.make_invoice(freight=200000, tax=10000)
        services.post_customer_invoice(invoice)
        bill = VendorBill.objects.create(number="BILL-R", vendor=self.vendor, taxable_amount=80000,
                                         gst_amount=4000, tds_amount=1600)
        services.post_vendor_bill(bill)

    def test_trial_balance_balances(self):
        response = self.client.get("/api/v1/accounting/reports/trial-balance/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["balanced"])
        self.assertEqual(response.data["total_debit"], response.data["total_credit"])

    def test_profit_and_loss_nets_income_against_expenses(self):
        response = self.client.get("/api/v1/accounting/reports/profit-and-loss/")
        self.assertEqual(response.data["total_income"], 200000.0)
        self.assertEqual(response.data["total_expense"], 80000.0)
        self.assertEqual(response.data["net_profit"], 120000.0)
        self.assertEqual(response.data["margin_percent"], 60.0)

    def test_ledger_shows_a_running_balance(self):
        response = self.client.get("/api/v1/accounting/reports/ledger/", {"account": "1200"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["closing_balance"], 210000.0)
        self.assertEqual(response.data["entries"][-1]["balance"], 210000.0)

    def test_ledger_rejects_an_unknown_account(self):
        self.assertEqual(self.client.get("/api/v1/accounting/reports/ledger/", {"account": "9999"}).status_code, 400)
        self.assertEqual(self.client.get("/api/v1/accounting/reports/ledger/").status_code, 400)

    def test_receivable_ageing_buckets_by_due_date(self):
        overdue = self.make_invoice(freight=50000, tax=2500)
        overdue.due_date = timezone.localdate() - timedelta(days=45)
        overdue.save(update_fields=["due_date"])
        response = self.client.get("/api/v1/accounting/reports/receivable-ageing/")
        party = response.data["parties"][0]
        self.assertEqual(party["party"], "Tata Consumer")
        self.assertEqual(party["31_60"], 52500.0)
        self.assertEqual(party["current"], 210000.0)

    def test_payable_ageing_reports_the_vendor_balance(self):
        response = self.client.get("/api/v1/accounting/reports/payable-ageing/")
        self.assertEqual(response.data["total_outstanding"], 82400.0)     # 80000 + 4000 - 1600

    def test_gst_summary_nets_output_against_input(self):
        response = self.client.get("/api/v1/accounting/reports/gst-summary/")
        self.assertEqual(response.data["output_gst"], 10000.0)
        self.assertEqual(response.data["input_gst"], 4000.0)
        self.assertEqual(response.data["net_payable"], 6000.0)

    def test_vehicle_profitability_nets_revenue_against_running_cost(self):
        area = ServiceArea.objects.create(name="West", code="W")
        pickup = Place.objects.create(name="A", code="A", city="Bhiwandi", service_area=area)
        drop = Place.objects.create(name="B", code="B", city="Chakan", service_area=area)
        order = Order.objects.create(number="ORD-1", customer=self.customer, pickup=pickup, dropoff=drop,
                                     vehicle=self.vehicle, total_amount=90000, status="completed",
                                     completed_at=timezone.now())
        FuelEntry.objects.create(vehicle=self.vehicle, odometer_km=1000, volume_litres=200, rate_per_litre=94)
        TripExpense.objects.create(vehicle=self.vehicle, category="toll", amount=3000)
        response = self.client.get("/api/v1/accounting/reports/vehicle-profitability/")
        row = response.data["vehicles"][0]
        self.assertEqual(row["vehicle"], "MH 04 JU 9182")
        self.assertEqual(row["revenue"], 90000.0)
        self.assertEqual(row["cost"], 21800.0)                            # 18800 diesel + 3000 toll
        self.assertEqual(row["profit"], 68200.0)


class PaymentAllocationTests(AccountingTestCase):
    def test_allocating_more_than_the_payment_is_rejected(self):
        invoice = self.make_invoice(freight=10000, tax=500)
        response = self.client.post("/api/v1/accounting/payments/", {
            "payment_type": "receipt", "customer": self.customer.id, "bank_account": self.bank.id,
            "amount": "5000", "mode": "neft",
            "allocations": [{"invoice": invoice.id, "amount": "9000"}]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("exceeds", str(response.data))

    def test_allocation_marks_a_bill_paid_and_tracks_the_balance(self):
        bill = VendorBill.objects.create(number="BILL-P", vendor=self.vendor, taxable_amount=10000, gst_amount=500)
        response = self.client.post("/api/v1/accounting/payments/", {
            "payment_type": "payment", "vendor": self.vendor.id, "bank_account": self.bank.id,
            "amount": "10500", "mode": "rtgs",
            "allocations": [{"bill": bill.id, "amount": "10500"}]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        bill.refresh_from_db()
        self.assertEqual(bill.paid_amount, Decimal("10500.00"))
        self.assertEqual(bill.status, "paid")
        self.assertEqual(bill.balance_due, Decimal("0.00"))

    def test_partial_allocation_leaves_the_bill_part_paid(self):
        bill = VendorBill.objects.create(number="BILL-Q", vendor=self.vendor, taxable_amount=20000, gst_amount=1000)
        self.client.post("/api/v1/accounting/payments/", {
            "payment_type": "payment", "vendor": self.vendor.id, "bank_account": self.bank.id,
            "amount": "5000", "mode": "upi", "allocations": [{"bill": bill.id, "amount": "5000"}]}, format="json")
        bill.refresh_from_db()
        self.assertEqual(bill.status, "part_paid")
        self.assertEqual(bill.balance_due, Decimal("16000.00"))


class QueryFilterTests(AccountingTestCase):
    """Query strings arrive as text; Django's BooleanField rejects a lowercase 'true'."""

    def test_boolean_filters_accept_query_string_values(self):
        for value in ("true", "True", "1"):
            response = self.client.get("/api/v1/accounting/accounts/", {"is_bank": value})
            self.assertEqual(response.status_code, 200, f"is_bank={value}")
            self.assertTrue(all(row["is_bank"] for row in response.data["results"]))
            self.assertEqual(response.data["count"], 2)          # cash in hand and bank accounts
        negative = self.client.get("/api/v1/accounting/accounts/", {"is_bank": "false"})
        self.assertEqual(negative.data["count"], 27)

    def test_a_nonsense_filter_value_is_a_client_error_not_a_crash(self):
        response = self.client.get("/api/v1/accounting/accounts/", {"is_bank": "banana"})
        self.assertEqual(response.status_code, 400)

    def test_date_window_filters_the_voucher_list(self):
        services.create_entry(source="manual", narration="Old", entry_date=timezone.localdate() - timedelta(days=90),
                              lines=[(self.bank, 100, 0, None, ""), (Account.objects.get(code="4100"), 0, 100, None, "")])
        services.create_entry(source="manual", narration="Recent", entry_date=timezone.localdate(),
                              lines=[(self.bank, 200, 0, None, ""), (Account.objects.get(code="4100"), 0, 200, None, "")])
        recent = self.client.get("/api/v1/accounting/journal-entries/",
                                 {"from": str(timezone.localdate() - timedelta(days=7))})
        self.assertEqual(recent.data["count"], 1)
        self.assertEqual(recent.data["results"][0]["narration"], "Recent")


class GroupAccountTests(AccountingTestCase):
    """A heading such as "Expenses" collects its children; posting to it corrupts the books."""

    def test_a_group_account_reports_itself_as_one(self):
        self.assertTrue(Account.objects.get(code="5000").is_group)     # Expenses, has children
        self.assertFalse(Account.objects.get(code="5100").is_group)    # Diesel, a leaf

    def test_the_api_refuses_a_line_posted_to_a_group(self):
        response = self.client.post("/api/v1/accounting/journal-entries/", {
            "narration": "Wrong level", "lines": [
                {"account": Account.objects.get(code="5000").id, "debit": "1000", "credit": "0"},
                {"account": self.bank.id, "debit": "0", "credit": "1000"}]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("group heading", str(response.data))

    def test_the_posting_rules_refuse_a_group_too(self):
        with self.assertRaises(services.PostingError):
            services.create_entry(source="manual", narration="Wrong level",
                                  lines=[(Account.objects.get(code="5000"), 1000, 0, None, ""),
                                         (self.bank, 0, 1000, None, "")])

    def test_a_leaf_account_still_posts(self):
        response = self.client.post("/api/v1/accounting/journal-entries/", {
            "narration": "Diesel purchase", "lines": [
                {"account": Account.objects.get(code="5100").id, "debit": "1000", "credit": "0"},
                {"account": self.bank.id, "debit": "0", "credit": "1000"}]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
