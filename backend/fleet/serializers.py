from rest_framework import serializers
from .models import (Customer, Driver, Vehicle, VehicleSize, VehicleType, LorryReceipt, Trip, TrackingEvent, Invoice, Settlement, SalesQuote,
                     MaintenanceWorkOrder, VehicleStatusLog, LOAD_TYPES, EXPENSE_CATEGORY_CODES)

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
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")
    current_place_name = serializers.CharField(source="current_place.name", read_only=True, default="")
    current_trip_number = serializers.CharField(source="current_trip.number", read_only=True, default="")
    class Meta: model = Vehicle; fields = "__all__"; read_only_fields = ["status_since"]


class VehicleSizeSerializer(serializers.ModelSerializer):
    class Meta: model = VehicleSize; fields = "__all__"


class VehicleTypeSerializer(serializers.ModelSerializer):
    class Meta: model = VehicleType; fields = "__all__"


class VehicleStatusLogSerializer(serializers.ModelSerializer):
    place_name = serializers.CharField(source="place.name", read_only=True, default="")
    trip_number = serializers.CharField(source="trip.number", read_only=True, default="")
    class Meta: model = VehicleStatusLog; fields = "__all__"
class LorryReceiptSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    class Meta: model = LorryReceipt; fields = "__all__"


class GenerateLorryReceiptInputSerializer(serializers.Serializer):
    """Optional operator-supplied identity for an LR generated from an order."""
    number = serializers.CharField(required=False, allow_blank=False, max_length=30, trim_whitespace=True)

    def validate_number(self, value):
        if LorryReceipt.objects.filter(number__iexact=value).exists():
            raise serializers.ValidationError("A lorry receipt with this number already exists.")
        return value


class TrackingEventSerializer(serializers.ModelSerializer):
    class Meta: model = TrackingEvent; fields = "__all__"
class TripSerializer(serializers.ModelSerializer):
    vehicle_number = serializers.CharField(source="vehicle.registration_number", read_only=True)
    driver_name = serializers.CharField(source="driver.name", read_only=True)
    tracking_events = TrackingEventSerializer(many=True, read_only=True)
    running_km = serializers.IntegerField(read_only=True)
    linked_orders = serializers.SerializerMethodField()
    order_count = serializers.SerializerMethodField()
    lorry_receipt_count = serializers.SerializerMethodField()
    customer_names = serializers.SerializerMethodField()
    class Meta:
        model = Trip; fields = "__all__"
        # A trip sheet is often opened before consignments are attached to it.
        extra_kwargs = {"lorry_receipts": {"required": False, "allow_empty": True}}

    def get_linked_orders(self, trip):
        return [
            {"id": o.id, "number": o.number, "status": o.status,
             "customer_name": o.customer.name,
             "route": f"{o.pickup.city} → {o.dropoff.city}"}
            for o in trip.orders.select_related("customer", "pickup", "dropoff").all()
        ]

    def get_order_count(self, trip):
        return len(trip.orders.all())

    def get_lorry_receipt_count(self, trip):
        return len(trip.lorry_receipts.all())

    def get_customer_names(self, trip):
        """A trip may be built from orders, legacy LRs, or both.

        Give the dispatch board one de-duplicated customer list regardless of how
        the trip was created, while keeping the first-seen operational order.
        """
        names = [order.customer.name for order in trip.orders.all()]
        names.extend(receipt.customer.name for receipt in trip.lorry_receipts.all())
        return list(dict.fromkeys(name for name in names if name))


class TripSettlementInputSerializer(serializers.Serializer):
    """One consolidated submission for everything a transport office's paper trip
    sheet captures: load type, dates, distance, odometer, freight and every expense
    line item, keyed by the same category codes as `TripExpense.category`.
    """
    load_type = serializers.ChoiceField(choices=LOAD_TYPES, required=False, allow_blank=True)
    load_date = serializers.DateField(required=False, allow_null=True)
    unload_date = serializers.DateField(required=False, allow_null=True)
    google_km = serializers.IntegerField(required=False, min_value=0)
    passed_km = serializers.IntegerField(required=False, min_value=0)
    start_odometer_km = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    end_odometer_km = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    freight_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    diesel_given = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    expenses = serializers.DictField(child=serializers.DecimalField(max_digits=12, decimal_places=2), required=False)

    def validate_expenses(self, value):
        unknown = sorted(set(value) - set(EXPENSE_CATEGORY_CODES))
        if unknown:
            raise serializers.ValidationError(f"Unknown expense categories: {', '.join(unknown)}")
        return value
class InvoiceSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    order_number = serializers.CharField(source="order.number", read_only=True, default="")
    trip_number = serializers.CharField(source="trip.number", read_only=True, default="")
    class Meta:
        model = Invoice; fields = "__all__"
        # Derived in Invoice.save from freight + charges + tax, so it can never disagree.
        read_only_fields = ["total_amount"]
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
    order_number = serializers.CharField(source="order.number", read_only=True)
    tracking_number = serializers.CharField(source="order.tracking_number", read_only=True)
    customer_name = serializers.CharField(source="order.customer.name", read_only=True)
    destination = serializers.CharField(source="order.dropoff.city", read_only=True, default="")
    is_clean = serializers.BooleanField(read_only=True)
    otp_expired = serializers.BooleanField(read_only=True)
    courier_overdue = serializers.BooleanField(read_only=True)
    class Meta:
        model = ProofOfDelivery; fields = "__all__"
        # The office issues and clears these; they are never set by hand on the record.
        read_only_fields = ["otp", "otp_issued_at", "otp_verified", "status", "verified_at", "verified_by",
                           "courier_status", "courier_dispatched_at", "courier_received_at"]


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
    progress_percent = serializers.IntegerField(read_only=True)
    last_position = serializers.SerializerMethodField()
    pickup_latitude = serializers.DecimalField(source="pickup.latitude", max_digits=9, decimal_places=6, read_only=True, default=None)
    pickup_longitude = serializers.DecimalField(source="pickup.longitude", max_digits=9, decimal_places=6, read_only=True, default=None)
    dropoff_latitude = serializers.DecimalField(source="dropoff.latitude", max_digits=9, decimal_places=6, read_only=True, default=None)
    dropoff_longitude = serializers.DecimalField(source="dropoff.longitude", max_digits=9, decimal_places=6, read_only=True, default=None)
    class Meta: model = Order; fields = "__all__"; read_only_fields = ["tracking_number"]

    def get_last_position(self, order):
        position = order.current_position()
        if not position:
            return None
        return {"city": position.city, "latitude": position.latitude, "longitude": position.longitude,
               "code": position.code, "details": position.details, "recorded_at": position.recorded_at}


class PublicOrderTrackingSerializer(serializers.ModelSerializer):
    """Consignment status shared with consignees. Deliberately omits pricing."""
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    origin = serializers.CharField(source="pickup.city", read_only=True)
    destination = serializers.CharField(source="dropoff.city", read_only=True)
    activities = TrackingActivitySerializer(many=True, read_only=True)
    progress_percent = serializers.IntegerField(read_only=True)
    last_position = serializers.SerializerMethodField()
    class Meta:
        model = Order
        fields = ["number", "tracking_number", "customer_name", "origin", "destination", "order_type", "status",
                  "packages", "weight_kg", "scheduled_at", "dispatched_at", "completed_at", "activities",
                  "progress_percent", "last_position"]

    def get_last_position(self, order):
        position = order.current_position()
        if not position:
            return None
        return {"city": position.city, "recorded_at": position.recorded_at}


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


class ProjectionRequestSerializer(QuoteRequestSerializer):
    """Input for the lane projection: the quote inputs, plus what it costs to run."""
    trips_per_month = serializers.IntegerField(required=False, default=1, min_value=1)
    vehicle = serializers.PrimaryKeyRelatedField(queryset=Vehicle.objects.all(), required=False, allow_null=True)
    diesel_price = serializers.DecimalField(max_digits=8, decimal_places=2, required=False, allow_null=True)
    mileage_kmpl = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    days = serializers.IntegerField(required=False, default=90, min_value=1, max_value=730)


from .models import Indent, VehicleHire, VendorLaneRate


class VehicleHireSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.number", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    trip_number = serializers.CharField(source="trip.number", read_only=True, default="")
    class Meta: model = VehicleHire; fields = "__all__"


class VendorLaneRateSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    class Meta: model = VendorLaneRate; fields = "__all__"


class IndentSerializer(serializers.ModelSerializer):
    number = serializers.CharField(max_length=30, required=False, allow_blank=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    pickup_city = serializers.CharField(source="pickup.city", read_only=True)
    dropoff_city = serializers.CharField(source="dropoff.city", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True, default="")
    vehicle_number = serializers.CharField(source="vehicle.registration_number", read_only=True, default="")
    driver_name = serializers.CharField(source="driver.name", read_only=True, default="")
    order_number = serializers.CharField(source="order.number", read_only=True, default="")
    class Meta: model = Indent; fields = "__all__"

    def validate(self, attrs):
        minimum = attrs.get("temp_min_c", getattr(self.instance, "temp_min_c", None))
        maximum = attrs.get("temp_max_c", getattr(self.instance, "temp_max_c", None))
        if minimum is not None and maximum is not None and minimum > maximum:
            raise serializers.ValidationError({"temp_max_c": "Maximum temperature must be at least the minimum temperature."})
        expected_delivery = attrs.get("expected_delivery_at", getattr(self.instance, "expected_delivery_at", None))
        required_at = attrs.get("required_at", getattr(self.instance, "required_at", None))
        if expected_delivery and required_at and expected_delivery < required_at:
            raise serializers.ValidationError({"expected_delivery_at": "Expected delivery cannot be before the vehicle is required."})
        return attrs
