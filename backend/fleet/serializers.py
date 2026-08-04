from rest_framework import serializers
from .models import Customer, Driver, Vehicle, LorryReceipt, Trip, TrackingEvent, Invoice, Settlement, SalesQuote, MaintenanceWorkOrder

class MaintenanceWorkOrderSerializer(serializers.ModelSerializer):
    vehicle_number = serializers.CharField(source="vehicle.registration_number", read_only=True)
    class Meta: model = MaintenanceWorkOrder; fields = "__all__"
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
    class Meta:
        model = Trip; fields = "__all__"
        # A trip sheet is often opened before consignments are attached to it.
        extra_kwargs = {"lorry_receipts": {"required": False, "allow_empty": True}}
class InvoiceSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    class Meta: model = Invoice; fields = "__all__"
class SettlementSerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source="driver.name", read_only=True)
    class Meta: model = Settlement; fields = "__all__"


# --- Fleetbase FleetOps inspired serializers -------------------------------
from .models import (Vendor, ServiceArea, Zone, Place, Fleet, ServiceRate, ServiceQuote, Order, Waypoint,
                     TrackingActivity, ProofOfDelivery, FuelEntry, TripExpense, Issue, ComplianceDocument,
                     MaintenanceSchedule)


class VendorSerializer(serializers.ModelSerializer):
    class Meta: model = Vendor; fields = "__all__"


class ServiceAreaSerializer(serializers.ModelSerializer):
    zone_count = serializers.IntegerField(source="zones.count", read_only=True)
    state_list = serializers.ListField(read_only=True)
    class Meta: model = ServiceArea; fields = "__all__"


class ZoneSerializer(serializers.ModelSerializer):
    service_area_name = serializers.CharField(source="service_area.name", read_only=True)
    class Meta: model = Zone; fields = "__all__"


class PlaceSerializer(serializers.ModelSerializer):
    service_area_name = serializers.CharField(source="service_area.name", read_only=True, default="")
    customer_name = serializers.CharField(source="customer.name", read_only=True, default="")
    class Meta: model = Place; fields = "__all__"


class FleetSerializer(serializers.ModelSerializer):
    service_area_name = serializers.CharField(source="service_area.name", read_only=True, default="")
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")
    vehicle_count = serializers.IntegerField(source="vehicles.count", read_only=True)
    driver_count = serializers.IntegerField(source="drivers.count", read_only=True)
    class Meta: model = Fleet; fields = "__all__"


class ServiceRateSerializer(serializers.ModelSerializer):
    service_area_name = serializers.CharField(source="service_area.name", read_only=True, default="")
    customer_name = serializers.CharField(source="customer.name", read_only=True, default="")
    class Meta: model = ServiceRate; fields = "__all__"


class ServiceQuoteSerializer(serializers.ModelSerializer):
    rate_card_name = serializers.CharField(source="service_rate.name", read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True, default="")
    class Meta: model = ServiceQuote; fields = "__all__"


class WaypointSerializer(serializers.ModelSerializer):
    place_name = serializers.CharField(source="place.name", read_only=True)
    city = serializers.CharField(source="place.city", read_only=True)
    class Meta: model = Waypoint; fields = "__all__"


class TrackingActivitySerializer(serializers.ModelSerializer):
    class Meta: model = TrackingActivity; fields = "__all__"


class ProofOfDeliverySerializer(serializers.ModelSerializer):
    class Meta: model = ProofOfDelivery; fields = "__all__"


class OrderSerializer(serializers.ModelSerializer):
    number = serializers.CharField(max_length=30, required=False, allow_blank=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    pickup_name = serializers.CharField(source="pickup.name", read_only=True)
    pickup_city = serializers.CharField(source="pickup.city", read_only=True)
    dropoff_name = serializers.CharField(source="dropoff.name", read_only=True)
    dropoff_city = serializers.CharField(source="dropoff.city", read_only=True)
    driver_name = serializers.CharField(source="driver.name", read_only=True, default="")
    vehicle_number = serializers.CharField(source="vehicle.registration_number", read_only=True, default="")
    fleet_name = serializers.CharField(source="fleet.name", read_only=True, default="")
    waypoints = WaypointSerializer(many=True, read_only=True)
    activities = TrackingActivitySerializer(many=True, read_only=True)
    proofs = ProofOfDeliverySerializer(many=True, read_only=True)
    class Meta: model = Order; fields = "__all__"; read_only_fields = ["tracking_number"]


class PublicOrderTrackingSerializer(serializers.ModelSerializer):
    """Consignment status shared with consignees. Deliberately omits pricing."""
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    origin = serializers.CharField(source="pickup.city", read_only=True)
    destination = serializers.CharField(source="dropoff.city", read_only=True)
    activities = TrackingActivitySerializer(many=True, read_only=True)
    class Meta:
        model = Order
        fields = ["number", "tracking_number", "customer_name", "origin", "destination", "order_type", "status",
                  "packages", "weight_kg", "scheduled_at", "dispatched_at", "completed_at", "activities"]


class FuelEntrySerializer(serializers.ModelSerializer):
    vehicle_number = serializers.CharField(source="vehicle.registration_number", read_only=True)
    driver_name = serializers.CharField(source="driver.name", read_only=True, default="")
    class Meta: model = FuelEntry; fields = "__all__"; read_only_fields = ["mileage_kmpl"]


class TripExpenseSerializer(serializers.ModelSerializer):
    vehicle_number = serializers.CharField(source="vehicle.registration_number", read_only=True, default="")
    driver_name = serializers.CharField(source="driver.name", read_only=True, default="")
    trip_number = serializers.CharField(source="trip.number", read_only=True, default="")
    class Meta: model = TripExpense; fields = "__all__"


class IssueSerializer(serializers.ModelSerializer):
    number = serializers.CharField(max_length=30, required=False, allow_blank=True)
    vehicle_number = serializers.CharField(source="vehicle.registration_number", read_only=True, default="")
    driver_name = serializers.CharField(source="driver.name", read_only=True, default="")
    class Meta: model = Issue; fields = "__all__"


class ComplianceDocumentSerializer(serializers.ModelSerializer):
    vehicle_number = serializers.CharField(source="vehicle.registration_number", read_only=True, default="")
    driver_name = serializers.CharField(source="driver.name", read_only=True, default="")
    days_to_expiry = serializers.IntegerField(read_only=True)
    status = serializers.CharField(read_only=True)
    class Meta: model = ComplianceDocument; fields = "__all__"

    def validate(self, attrs):
        vehicle = attrs.get("vehicle", getattr(self.instance, "vehicle", None))
        driver = attrs.get("driver", getattr(self.instance, "driver", None))
        if not vehicle and not driver:
            raise serializers.ValidationError("A document must belong to either a vehicle or a driver.")
        return attrs


class MaintenanceScheduleSerializer(serializers.ModelSerializer):
    vehicle_number = serializers.CharField(source="vehicle.registration_number", read_only=True)
    km_remaining = serializers.IntegerField(read_only=True)
    is_due = serializers.BooleanField(read_only=True)
    class Meta: model = MaintenanceSchedule; fields = "__all__"; read_only_fields = ["next_due_km", "next_due_date"]


class QuoteRequestSerializer(serializers.Serializer):
    """Input for the rate-card freight estimator."""
    service_rate = serializers.PrimaryKeyRelatedField(queryset=ServiceRate.objects.all())
    origin = serializers.CharField(max_length=120, required=False, allow_blank=True)
    destination = serializers.CharField(max_length=120, required=False, allow_blank=True)
    distance_km = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, default=0)
    weight_kg = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    hours = serializers.DecimalField(max_digits=8, decimal_places=2, required=False, default=0)
    halt_days = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=0)
    other_charges = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    customer = serializers.PrimaryKeyRelatedField(queryset=Customer.objects.all(), required=False, allow_null=True)
    save_quote = serializers.BooleanField(required=False, default=False)
