from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token
from .views import (health, dashboard, fleet_analytics, public_tracking, CustomerViewSet, DriverViewSet, VehicleViewSet,
                    LorryReceiptViewSet, TripViewSet, TrackingEventViewSet, InvoiceViewSet, SettlementViewSet,
                    SalesQuoteViewSet, MaintenanceWorkOrderViewSet, VendorViewSet, ServiceAreaViewSet, ZoneViewSet,
                    PlaceViewSet, FleetViewSet, ServiceRateViewSet, ServiceQuoteViewSet, OrderViewSet, WaypointViewSet,
                    TrackingActivityViewSet, ProofOfDeliveryViewSet, FuelEntryViewSet, TripExpenseViewSet, IssueViewSet,
                    ComplianceDocumentViewSet, MaintenanceScheduleViewSet, IndentViewSet, VehicleHireViewSet,
                    order_profitability, report_driver_availability, report_fleet, report_customer_invoices,
                    report_vehicle_settlement, report_sales)

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
# Fleetbase FleetOps inspired resources
router.register("vendors", VendorViewSet)
router.register("service-areas", ServiceAreaViewSet)
router.register("zones", ZoneViewSet)
router.register("places", PlaceViewSet)
router.register("fleets", FleetViewSet)
router.register("service-rates", ServiceRateViewSet)
router.register("service-quotes", ServiceQuoteViewSet)
router.register("orders", OrderViewSet)
router.register("waypoints", WaypointViewSet)
router.register("tracking-activities", TrackingActivityViewSet)
router.register("proofs", ProofOfDeliveryViewSet)
router.register("fuel-entries", FuelEntryViewSet)
router.register("trip-expenses", TripExpenseViewSet)
router.register("issues", IssueViewSet)
router.register("compliance-documents", ComplianceDocumentViewSet)
router.register("maintenance-schedules", MaintenanceScheduleViewSet)
router.register("indents", IndentViewSet)
router.register("hires", VehicleHireViewSet)

urlpatterns = [
    path("health/", health),
    path("dashboard/", dashboard),
    path("analytics/fleet/", fleet_analytics),
    path("track/<str:tracking_number>/", public_tracking),
    path("orders/<int:pk>/profitability/", order_profitability),
    path("auth/token/", obtain_auth_token),
    # Reports
    path("reports/driver-availability/", report_driver_availability),
    path("reports/fleet/", report_fleet),
    path("reports/customer-invoices/", report_customer_invoices),
    path("reports/vehicle-settlement/", report_vehicle_settlement),
    path("reports/sales/", report_sales),
    path("", include(router.urls)),
]
