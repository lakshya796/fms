from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import GiftVoucherViewSet, VoucherBatchViewSet, voucher_by_number

router = DefaultRouter()
router.register("batches", VoucherBatchViewSet)
router.register("vouchers", GiftVoucherViewSet)

urlpatterns = [
    path("lookup/<str:number>/", voucher_by_number),
    path("", include(router.urls)),
]
