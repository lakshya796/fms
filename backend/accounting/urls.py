from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (AccountViewSet, CostCentreViewSet, FiscalYearViewSet, JournalEntryViewSet,
                    PaymentViewSet, VendorBillViewSet, account_ledger, gst_summary, payable_ageing,
                    profit_and_loss, receivable_ageing, trial_balance, vehicle_profitability)

router = DefaultRouter()
router.register("accounts", AccountViewSet)
router.register("cost-centres", CostCentreViewSet)
router.register("fiscal-years", FiscalYearViewSet)
router.register("journal-entries", JournalEntryViewSet)
router.register("vendor-bills", VendorBillViewSet)
router.register("payments", PaymentViewSet)

urlpatterns = [
    path("reports/trial-balance/", trial_balance),
    path("reports/profit-and-loss/", profit_and_loss),
    path("reports/ledger/", account_ledger),
    path("reports/receivable-ageing/", receivable_ageing),
    path("reports/payable-ageing/", payable_ageing),
    path("reports/vehicle-profitability/", vehicle_profitability),
    path("reports/gst-summary/", gst_summary),
    path("", include(router.urls)),
]
