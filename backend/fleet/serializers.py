from rest_framework import serializers
from .models import Customer, Driver, Vehicle, LorryReceipt, Trip, TrackingEvent, Invoice, Settlement, SalesQuote

class SalesQuoteSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    class Meta: model = SalesQuote; fields = "__all__"
class CustomerSerializer(serializers.ModelSerializer):
    class Meta: model = Customer; fields = "__all__"
class DriverSerializer(serializers.ModelSerializer):
    class Meta: model = Driver; fields = "__all__"
class VehicleSerializer(serializers.ModelSerializer):
    class Meta: model = Vehicle; fields = "__all__"
class LorryReceiptSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    class Meta: model = LorryReceipt; fields = "__all__"
class TrackingEventSerializer(serializers.ModelSerializer):
    class Meta: model = TrackingEvent; fields = "__all__"
class TripSerializer(serializers.ModelSerializer):
    vehicle_number = serializers.CharField(source="vehicle.registration_number", read_only=True)
    driver_name = serializers.CharField(source="driver.name", read_only=True)
    tracking_events = TrackingEventSerializer(many=True, read_only=True)
    class Meta: model = Trip; fields = "__all__"
class InvoiceSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    class Meta: model = Invoice; fields = "__all__"
class SettlementSerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source="driver.name", read_only=True)
    class Meta: model = Settlement; fields = "__all__"
