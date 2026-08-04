from django.contrib import admin
from .models import Account, CostCentre, FiscalYear, JournalEntry, JournalLine, Payment, PaymentAllocation, VendorBill

admin.site.register([Account, CostCentre, FiscalYear, JournalEntry, JournalLine, Payment, PaymentAllocation, VendorBill])
