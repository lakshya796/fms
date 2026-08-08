from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (DepartmentViewSet, NotificationViewSet, PortalBatchViewSet, PortalUserAccessViewSet,
                    PortalVoucherViewSet, ReportsViewSet, VoucherPrefixViewSet, VoucherTemplateViewSet,
                    VoucherTypeViewSet)

router = DefaultRouter()
router.register("departments", DepartmentViewSet)
router.register("voucher-types", VoucherTypeViewSet)
router.register("prefixes", VoucherPrefixViewSet)
router.register("templates", VoucherTemplateViewSet)
router.register("batches", PortalBatchViewSet)
router.register("vouchers", PortalVoucherViewSet)
router.register("access", PortalUserAccessViewSet)
router.register("notifications", NotificationViewSet, basename="notification")
router.register("reports", ReportsViewSet, basename="report")

urlpatterns = [
    path("", include(router.urls)),
]
