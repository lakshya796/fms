from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (DepartmentViewSet, PortalBatchViewSet, PortalVoucherViewSet, VoucherPrefixViewSet,
                    VoucherTemplateViewSet, VoucherTypeViewSet)

router = DefaultRouter()
router.register("departments", DepartmentViewSet)
router.register("voucher-types", VoucherTypeViewSet)
router.register("prefixes", VoucherPrefixViewSet)
router.register("templates", VoucherTemplateViewSet)
router.register("batches", PortalBatchViewSet)
router.register("vouchers", PortalVoucherViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
