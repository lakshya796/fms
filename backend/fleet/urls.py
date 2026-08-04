from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token
from .views import health, dashboard, CustomerViewSet, DriverViewSet, VehicleViewSet, LorryReceiptViewSet, TripViewSet, TrackingEventViewSet, InvoiceViewSet, SettlementViewSet, SalesQuoteViewSet, MaintenanceWorkOrderViewSet
router = DefaultRouter()
router.register("maintenance", MaintenanceWorkOrderViewSet)
router.register("quotes", SalesQuoteViewSet)
router.register("customers", CustomerViewSet)
router.register("drivers", DriverViewSet)
router.register("vehicles", VehicleViewSet)
router.register("lorry-receipts", LorryReceiptViewSet)
router.register("trips", TripViewSet)
router.register("tracking-events", TrackingEventViewSet)
router.register("invoices", InvoiceViewSet)
router.register("settlements", SettlementViewSet)
urlpatterns = [path("health/", health), path("dashboard/", dashboard), path("auth/token/", obtain_auth_token), path("", include(router.urls))]
