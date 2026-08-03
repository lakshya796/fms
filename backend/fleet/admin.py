from django.contrib import admin
from .models import Customer, Driver, Vehicle, LorryReceipt, Trip, TrackingEvent, Invoice, Settlement
admin.site.register([Customer, Driver, Vehicle, LorryReceipt, Trip, TrackingEvent, Invoice, Settlement])
