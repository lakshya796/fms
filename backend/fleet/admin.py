from django.contrib import admin
from .models import (Customer, Driver, Vehicle, LorryReceipt, Trip, TrackingEvent, Invoice, Settlement, SalesQuote,
                     MaintenanceWorkOrder, Vendor, ServiceArea, Zone, Place, Fleet, ServiceRate, ServiceQuote, Order,
                     Waypoint, TrackingActivity, ProofOfDelivery, FuelEntry, TripExpense, Issue, ComplianceDocument,
                     MaintenanceSchedule)

admin.site.register([Customer, Driver, Vehicle, LorryReceipt, Trip, TrackingEvent, Invoice, Settlement, SalesQuote,
                     MaintenanceWorkOrder, Vendor, ServiceArea, Zone, Place, Fleet, ServiceRate, ServiceQuote, Order,
                     Waypoint, TrackingActivity, ProofOfDelivery, FuelEntry, TripExpense, Issue, ComplianceDocument,
                     MaintenanceSchedule])
