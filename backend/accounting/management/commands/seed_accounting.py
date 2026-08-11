"""Create the standard chart of accounts, roles and branches for an Indian transporter.

Usage: python manage.py seed_accounting

Idempotent: safe to run against an existing database, it only fills gaps.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounting.models import Account, FiscalYear
from iam.models import Branch, Organisation, PERMISSION_CODES, Role

CHART = [
    ("1000", "Assets", "asset", None, False),
    ("1100", "Current assets", "asset", "1000", False),
    ("1110", "Cash in hand", "asset", "1100", True),
    ("1120", "Bank accounts", "asset", "1100", True),
    ("1200", "Sundry debtors", "asset", "1100", False),
    ("1300", "Input GST", "asset", "1100", False),
    ("1400", "Driver advances", "asset", "1100", False),
    ("1500", "Fixed assets", "asset", "1000", False),
    ("1510", "Vehicles", "asset", "1500", False),
    ("2000", "Liabilities", "liability", None, False),
    ("2100", "Sundry creditors", "liability", "2000", False),
    ("2200", "Output GST", "liability", "2000", False),
    ("2300", "TDS payable", "liability", "2000", False),
    ("2400", "Vehicle loans", "liability", "2000", False),
    ("3000", "Equity", "equity", None, False),
    ("3100", "Owner capital", "equity", "3000", False),
    ("4000", "Income", "income", None, False),
    ("4100", "Freight income", "income", "4000", False),
    ("4200", "Detention and other income", "income", "4000", False),
    ("5000", "Expenses", "expense", None, False),
    ("5100", "Diesel", "expense", "5000", False),
    ("5200", "Toll and FASTag", "expense", "5000", False),
    ("5300", "Driver salary and bhatta", "expense", "5000", False),
    ("5400", "Vehicle maintenance", "expense", "5000", False),
    ("5500", "Tyres", "expense", "5000", False),
    ("5600", "Hired vehicle freight", "expense", "5000", False),
    ("5700", "Loading and unloading", "expense", "5000", False),
    ("5800", "RTO, permits and fines", "expense", "5000", False),
    ("5900", "Administrative expenses", "expense", "5000", False),
]

ROLES = [
    ("Administrator", "admin", "Full access to every module", PERMISSION_CODES),
    ("Branch manager", "branch_manager", "Runs a branch end to end", [
        "operations.view", "operations.manage", "masters.view", "masters.manage", "rates.view",
        "maintenance.view", "maintenance.manage", "compliance.view", "compliance.manage",
        "expenses.view", "expenses.manage", "expenses.approve", "accounting.view", "reports.view",
        "dispatch.view", "dispatch.plan", "dispatch.commit"]),
    ("Dispatcher", "dispatcher", "Books, allocates and dispatch-plans consignments", [
        "operations.view", "operations.manage", "masters.view", "rates.view", "reports.view",
        "dispatch.view", "dispatch.plan", "dispatch.commit"]),
    ("Accounts executive", "accounts", "Billing, collections and vouchers", [
        "operations.view", "masters.view", "rates.view", "accounting.view", "accounting.manage",
        "expenses.view", "expenses.approve", "reports.view"]),
    ("Accounts manager", "accounts_manager", "Posts and reverses journal entries", [
        "operations.view", "masters.view", "rates.view", "rates.manage", "accounting.view",
        "accounting.manage", "accounting.post", "expenses.view", "expenses.approve", "reports.view"]),
    ("Workshop supervisor", "workshop", "Maintenance and compliance", [
        "masters.view", "maintenance.view", "maintenance.manage", "compliance.view",
        "compliance.manage", "expenses.view", "reports.view"]),
    ("Viewer", "viewer", "Read only across operations", [
        "operations.view", "masters.view", "rates.view", "maintenance.view", "compliance.view",
        "expenses.view", "reports.view"]),
]


class Command(BaseCommand):
    help = "Seed the chart of accounts, standard roles, a head office branch and the current fiscal year"

    @transaction.atomic
    def handle(self, *args, **options):
        created_accounts = 0
        for code, name, account_type, parent_code, is_bank in CHART:
            parent = Account.objects.filter(code=parent_code).first() if parent_code else None
            _, created = Account.objects.get_or_create(
                code=code, defaults={"name": name, "account_type": account_type, "parent": parent, "is_bank": is_bank})
            created_accounts += int(created)

        created_roles = 0
        for name, code, description, permissions in ROLES:
            _, created = Role.objects.get_or_create(
                code=code, defaults={"name": name, "description": description,
                                     "permissions": permissions, "is_system": True})
            created_roles += int(created)

        organisation, _ = Organisation.objects.get_or_create(
            name="Phloz Transport", defaults={"legal_name": "Phloz Transport Private Limited",
                                              "fiscal_year_start_month": 4})
        Branch.objects.get_or_create(code="HO", defaults={
            "organisation": organisation, "name": "Head office", "branch_type": "head_office", "city": "Mumbai",
            "state": "Maharashtra"})

        today = timezone.localdate()
        start_year = today.year if today.month >= 4 else today.year - 1
        FiscalYear.objects.get_or_create(
            name=f"{start_year}-{str(start_year + 1)[-2:]}",
            defaults={"start_date": f"{start_year}-04-01", "end_date": f"{start_year + 1}-03-31"})

        if options.get("verbosity", 1) == 0:
            return
        self.stdout.write(self.style.SUCCESS(
            f"Chart of accounts: {Account.objects.count()} accounts ({created_accounts} new). "
            f"Roles: {Role.objects.count()} ({created_roles} new). "
            f"Branches: {Branch.objects.count()}. Fiscal years: {FiscalYear.objects.count()}."))
