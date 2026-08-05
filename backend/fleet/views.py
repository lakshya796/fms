from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.decorators import permission_classes
from iam.filtering import apply_filters
from iam.permissions import HasModulePermission, requires
from .models import Customer, Driver, Vehicle, LorryReceipt, Trip, TrackingEvent, Invoice, Settlement, SalesQuote, MaintenanceWorkOrder
from .serializers import CustomerSerializer, DriverSerializer, VehicleSerializer, LorryReceiptSerializer, TripSerializer, TrackingEventSerializer, InvoiceSerializer, SettlementSerializer, SalesQuoteSerializer, MaintenanceWorkOrderSerializer

@requires("reports.view")
@api_view(["GET"])
def dashboard(request):
    recent = Trip.objects.select_related("vehicle", "driver").order_by("-created_at")[:6]
    return Response({
        "customers": Customer.objects.count(),
        "kyc_pending": Customer.objects.filter(kyc_status="pending").count(),
        "vehicles": Vehicle.objects.count(),
        "vehicles_on_trip": Vehicle.objects.filter(status="on_trip").count(),
        "available_vehicles": Vehicle.objects.filter(status="available").count(),
        "active_trips": Trip.objects.exclude(status="closed").count(),
        "lorry_receipts": LorryReceipt.objects.count(),
        "open_invoices": Invoice.objects.exclude(status="paid").count(),
        "invoice_total": Invoice.objects.aggregate(value=Sum("total_amount"))["value"] or 0,
        "pending_settlements": Settlement.objects.filter(status="pending").aggregate(value=Sum("net_payable"))["value"] or 0,
        "recent_trips": TripSerializer(recent, many=True).data,
        **fleetops_dashboard_metrics(),
    })


def fleetops_dashboard_metrics():
    """FleetOps counters surfaced next to the classic transport ERP numbers."""
    from datetime import timedelta
    today = timezone.localdate()
    return {
        "orders": Order.objects.count(),
        "active_orders": Order.objects.exclude(status__in=["completed", "cancelled"]).count(),
        "orders_completed": Order.objects.filter(status="completed").count(),
        "order_revenue": Order.objects.aggregate(value=Sum("total_amount"))["value"] or 0,
        "fleets": Fleet.objects.count(),
        "vendors": Vendor.objects.count(),
        "places": Place.objects.count(),
        "service_areas": ServiceArea.objects.count(),
        "zones": Zone.objects.count(),
        "open_issues": Issue.objects.exclude(status="resolved").count(),
        "documents_expiring": ComplianceDocument.objects.filter(expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=30)).count(),
        "fuel_spend": FuelEntry.objects.filter(entry_date__gte=today - timedelta(days=30)).aggregate(value=Sum("amount"))["value"] or 0,
        "trip_expenses": TripExpense.objects.filter(expense_date__gte=today - timedelta(days=30)).aggregate(value=Sum("amount"))["value"] or 0,
    }

@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"status": "ok", "service": "phloz-fms-api", "time": timezone.now()})

class MaintenanceWorkOrderViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "maintenance.view"; required_write_permission = "maintenance.manage"
    queryset = MaintenanceWorkOrder.objects.select_related("vehicle").all().order_by("-created_at")
    serializer_class = MaintenanceWorkOrderSerializer
class SalesQuoteViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "rates.view"; required_write_permission = "rates.manage"
    queryset = SalesQuote.objects.select_related("customer").all().order_by("-created_at")
    serializer_class = SalesQuoteSerializer
class CustomerViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "masters.view"; required_write_permission = "masters.manage"
    queryset = Customer.objects.all().order_by("-created_at"); serializer_class = CustomerSerializer
class DriverViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "masters.view"; required_write_permission = "masters.manage"
    queryset = Driver.objects.all().order_by("name"); serializer_class = DriverSerializer
class VehicleViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "masters.view"; required_write_permission = "masters.manage"
    queryset = Vehicle.objects.all().order_by("registration_number"); serializer_class = VehicleSerializer
class LorryReceiptViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "operations.view"; required_write_permission = "operations.manage"
    queryset = LorryReceipt.objects.select_related("customer").all().order_by("-created_at"); serializer_class = LorryReceiptSerializer
class TrackingEventViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "operations.view"; required_write_permission = "operations.manage"
    queryset = TrackingEvent.objects.select_related("trip").all().order_by("-recorded_at"); serializer_class = TrackingEventSerializer
class TripViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "operations.view"; required_write_permission = "operations.manage"
    queryset = Trip.objects.select_related("vehicle", "driver").prefetch_related("lorry_receipts", "tracking_events").all().order_by("-created_at")
    serializer_class = TripSerializer
    @action(detail=True, methods=["post"], url_path="dispatch")
    @transaction.atomic
    def dispatch_trip(self, request, pk=None):
        trip = self.get_object()
        trip.status = "dispatched"; trip.actual_departure = timezone.now(); trip.save()
        trip.vehicle.status = "on_trip"; trip.vehicle.save(update_fields=["status"])
        trip.driver.status = "on_trip"; trip.driver.save(update_fields=["status"])
        trip.lorry_receipts.update(status="dispatched")
        return Response(self.get_serializer(trip).data)
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def close(self, request, pk=None):
        trip = self.get_object()
        trip.status = "closed"; trip.arrival_at = trip.arrival_at or timezone.now(); trip.save()
        trip.vehicle.status = "available"; trip.vehicle.save(update_fields=["status"])
        trip.driver.status = "available"; trip.driver.save(update_fields=["status"])
        trip.lorry_receipts.update(status="delivered")
        return Response(self.get_serializer(trip).data)
class InvoiceViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "accounting.view"; required_write_permission = "accounting.manage"
    queryset = Invoice.objects.select_related("customer", "trip").all().order_by("-created_at"); serializer_class = InvoiceSerializer
class SettlementViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "accounting.view"; required_write_permission = "accounting.manage"
    queryset = Settlement.objects.select_related("driver", "trip").all().order_by("-created_at"); serializer_class = SettlementSerializer


# --- Fleetbase FleetOps inspired endpoints ---------------------------------
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from django.db.models import Avg, Count, F, Q
from rest_framework import status as http_status
from rest_framework.exceptions import ValidationError
from .models import (Vendor, ServiceArea, Zone, Place, Fleet, ServiceRate, ServiceQuote, Order, Waypoint,
                     TrackingActivity, ProofOfDelivery, FuelEntry, TripExpense, Issue, ComplianceDocument,
                     MaintenanceSchedule, ORDER_STATUSES, haversine_km, money)
from .serializers import (VendorSerializer, ServiceAreaSerializer, ZoneSerializer, PlaceSerializer, FleetSerializer,
                          ServiceRateSerializer, ServiceQuoteSerializer, OrderSerializer, WaypointSerializer,
                          TrackingActivitySerializer, ProofOfDeliverySerializer, PublicOrderTrackingSerializer,
                          FuelEntrySerializer, TripExpenseSerializer, IssueSerializer, ComplianceDocumentSerializer,
                          MaintenanceScheduleSerializer, QuoteRequestSerializer, ProjectionRequestSerializer)
from .billing import BillingError, build_invoice_from_order, project_lane
from accounting.services import PostingError, post_customer_invoice


class FilterableViewSet(viewsets.ModelViewSet):
    """Adds `?field=value` filtering, `?search=`, and role based access."""
    permission_classes = [HasModulePermission]
    required_permission = "operations.view"
    required_write_permission = "operations.manage"
    filter_fields: list = []
    search_fields: list = []

    def get_queryset(self):
        return apply_filters(super().get_queryset(), self.request.query_params,
                             self.filter_fields, self.search_fields)


class VendorViewSet(FilterableViewSet):
    required_permission = "masters.view"; required_write_permission = "masters.manage"
    queryset = Vendor.objects.all()
    serializer_class = VendorSerializer
    filter_fields = ["vendor_type", "status", "state"]
    search_fields = ["name", "code", "city", "gstin"]


class ServiceAreaViewSet(FilterableViewSet):
    required_permission = "masters.view"; required_write_permission = "masters.manage"
    queryset = ServiceArea.objects.prefetch_related("zones").all()
    serializer_class = ServiceAreaSerializer
    filter_fields = ["status"]
    search_fields = ["name", "code", "states"]


class ZoneViewSet(FilterableViewSet):
    required_permission = "masters.view"; required_write_permission = "masters.manage"
    queryset = Zone.objects.select_related("service_area").all()
    serializer_class = ZoneSerializer
    filter_fields = ["service_area", "zone_type", "status"]
    search_fields = ["name"]

    @action(detail=False, methods=["get"])
    def locate(self, request):
        """Return every active zone containing the supplied coordinates."""
        try:
            latitude, longitude = float(request.query_params["lat"]), float(request.query_params["lng"])
        except (KeyError, ValueError):
            raise ValidationError("Provide numeric `lat` and `lng` query parameters.")
        matches = []
        for zone in self.get_queryset().filter(status="active"):
            distance = zone.distance_km(latitude, longitude)
            if distance <= float(zone.radius_km):
                matches.append({**ZoneSerializer(zone).data, "distance_km": distance})
        matches.sort(key=lambda item: item["distance_km"])
        return Response({"latitude": latitude, "longitude": longitude, "count": len(matches), "zones": matches})


class PlaceViewSet(FilterableViewSet):
    required_permission = "masters.view"; required_write_permission = "masters.manage"
    queryset = Place.objects.select_related("service_area", "customer").all()
    serializer_class = PlaceSerializer
    filter_fields = ["place_type", "city", "state", "service_area", "customer", "status"]
    search_fields = ["name", "code", "city", "pincode", "address"]


class FleetViewSet(FilterableViewSet):
    required_permission = "masters.view"; required_write_permission = "masters.manage"
    queryset = Fleet.objects.select_related("service_area", "vendor").prefetch_related("vehicles", "drivers").all()
    serializer_class = FleetSerializer
    filter_fields = ["service_area", "vendor", "status"]
    search_fields = ["name", "code", "manager"]

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        """Add or remove vehicles and drivers: {"vehicles": [1], "drivers": [2], "remove": false}."""
        fleet = self.get_object()
        remove = bool(request.data.get("remove"))
        for field, related in (("vehicles", fleet.vehicles), ("drivers", fleet.drivers)):
            ids = request.data.get(field) or []
            if ids:
                related.remove(*ids) if remove else related.add(*ids)
        return Response(self.get_serializer(fleet).data)


class ServiceRateViewSet(FilterableViewSet):
    required_permission = "rates.view"; required_write_permission = "rates.manage"
    queryset = ServiceRate.objects.select_related("service_area", "customer").all()
    serializer_class = ServiceRateSerializer
    filter_fields = ["service_area", "customer", "rate_type", "status"]
    search_fields = ["name", "code", "vehicle_type"]

    @action(detail=False, methods=["post"])
    def quote(self, request):
        """Price a lane from a rate card, optionally storing the quote."""
        form = QuoteRequestSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        rate = data["service_rate"]
        breakdown = rate.quote(distance_km=data.get("distance_km") or 0, weight_kg=data.get("weight_kg") or 0,
                               hours=data.get("hours") or 0, halt_days=data.get("halt_days") or 0,
                               other_charges=data.get("other_charges") or 0)
        payload = {"breakdown": breakdown, "quote": None}
        if data.get("save_quote"):
            quote = ServiceQuote.objects.create(
                number="SQ-" + timezone.now().strftime("%y%m%d") + uuid4().hex[:6].upper(), service_rate=rate, customer=data.get("customer"),
                origin=data.get("origin") or "", destination=data.get("destination") or "",
                distance_km=data.get("distance_km") or 0, weight_kg=data.get("weight_kg") or 0,
                breakdown=breakdown, total_amount=breakdown["total"],
                valid_until=timezone.localdate() + timedelta(days=15), status="sent")
            payload["quote"] = ServiceQuoteSerializer(quote).data
        return Response(payload)

    @action(detail=False, methods=["post"])
    def project(self, request):
        """Project what a lane earns against what it costs to run.

        The cost side comes from this fleet's own diesel and on-road spend, so the
        margin is the one the owner would actually see, not a guess.
        """
        form = ProjectionRequestSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        return Response(project_lane(
            data["service_rate"], distance_km=data.get("distance_km") or 0, weight_kg=data.get("weight_kg") or 0,
            hours=data.get("hours") or 0, halt_days=data.get("halt_days") or 0,
            other_charges=data.get("other_charges") or 0, trips_per_month=data.get("trips_per_month") or 1,
            vehicle=data.get("vehicle"), diesel_price=data.get("diesel_price"),
            mileage_kmpl=data.get("mileage_kmpl"), days=data.get("days") or 90))


class ServiceQuoteViewSet(FilterableViewSet):
    required_permission = "rates.view"; required_write_permission = "rates.manage"
    queryset = ServiceQuote.objects.select_related("service_rate", "customer").all()
    serializer_class = ServiceQuoteSerializer
    filter_fields = ["customer", "service_rate", "status"]
    search_fields = ["number", "origin", "destination"]


class OrderViewSet(FilterableViewSet):
    queryset = Order.objects.select_related("customer", "pickup", "dropoff", "driver", "vehicle", "fleet").prefetch_related("waypoints", "activities", "proofs").all()
    serializer_class = OrderSerializer
    filter_fields = ["status", "order_type", "customer", "driver", "vehicle", "fleet", "payment_mode"]
    search_fields = ["number", "tracking_number", "eway_bill_number", "payload_description"]

    def perform_create(self, serializer):
        order = serializer.save(number=serializer.validated_data.get("number") or "ORD-" + timezone.now().strftime("%y%m%d") + uuid4().hex[:6].upper())
        coordinates = [order.pickup.latitude, order.pickup.longitude, order.dropoff.latitude, order.dropoff.longitude]
        if all(value is not None for value in coordinates) and not order.distance_km:
            order.distance_km = haversine_km(order.pickup.latitude, order.pickup.longitude,
                                             order.dropoff.latitude, order.dropoff.longitude)
            order.save(update_fields=["distance_km"])
        if order.service_rate and not order.total_amount:
            order.price_from_rate_card()
        order.log("created", "ORDER_CREATED", f"Booking confirmed for {order.customer.name}", city=order.pickup.city)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def assign(self, request, pk=None):
        """Allocate a driver and vehicle (Fleetbase dispatch assignment)."""
        order = self.get_object()
        driver_id, vehicle_id = request.data.get("driver"), request.data.get("vehicle")
        if driver_id:
            order.driver = Driver.objects.get(pk=driver_id)
        if vehicle_id:
            order.vehicle = Vehicle.objects.get(pk=vehicle_id)
        if not order.driver or not order.vehicle:
            raise ValidationError("Both a driver and a vehicle are required to assign an order.")
        order.status = "assigned"
        order.save()
        order.log("assigned", "ORDER_ASSIGNED", f"{order.vehicle.registration_number} · {order.driver.name}")
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"], url_path="dispatch")
    @transaction.atomic
    def dispatch_order(self, request, pk=None):
        order = self.get_object()
        if not order.driver or not order.vehicle:
            raise ValidationError("Assign a driver and vehicle before dispatching.")
        order.status = "dispatched"
        order.dispatched_at = timezone.now()
        order.save()
        Vehicle.objects.filter(pk=order.vehicle_id).update(status="on_trip")
        Driver.objects.filter(pk=order.driver_id).update(status="on_trip")
        order.log("dispatched", "ORDER_DISPATCHED", f"Loaded at {order.pickup.name}", city=order.pickup.city)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def activity(self, request, pk=None):
        """Push a tracking update from the driver app or a telematics webhook."""
        order = self.get_object()
        code = request.data.get("code") or "STATUS_UPDATE"
        new_status = request.data.get("status") or order.status
        if new_status not in ORDER_STATUSES:
            raise ValidationError(f"status must be one of {ORDER_STATUSES}")
        activity = order.log(new_status, code, request.data.get("details", ""),
                             request.data.get("latitude"), request.data.get("longitude"), request.data.get("city", ""))
        if new_status != order.status:
            order.status = new_status
            order.save(update_fields=["status", "updated_at"])
        return Response(TrackingActivitySerializer(activity).data, status=http_status.HTTP_201_CREATED)

    # --- ePOD ---------------------------------------------------------------

    @staticmethod
    def _open_proof(order):
        """The proof still in play for this order, if there is one."""
        return order.proofs.filter(status__in=["awaiting", "rejected"]).order_by("-created_at").first()

    @staticmethod
    def _capture(proof, data):
        """Record what the driver captured at the drop and settle its review state."""
        supplied = str(data.get("otp") or "").strip()
        if proof.otp:
            if proof.otp_expired:
                raise ValidationError("The delivery OTP has expired. Issue a fresh one before capturing the ePOD.")
            if supplied != proof.otp:
                raise ValidationError("That OTP does not match the one issued to the consignee.")
            proof.otp_verified = True
        elif supplied:
            # No OTP was issued for this drop, so an office-side code cannot be trusted;
            # the capture stands on the signature or photo instead.
            proof.otp = supplied
        for field in ("proof_type", "receiver_name", "receiver_phone", "remarks", "file_url"):
            if data.get(field) is not None:
                setattr(proof, field, data.get(field) or getattr(proof, field))
        if data.get("shortage_kg") is not None:
            proof.shortage_kg = data.get("shortage_kg") or 0
        if data.get("damage_reported") is not None:
            proof.damage_reported = bool(data.get("damage_reported"))
        proof.latitude = data.get("latitude") or proof.latitude
        proof.longitude = data.get("longitude") or proof.longitude
        proof.rejection_reason = ""
        proof.captured_at = timezone.now()
        proof.settle()
        proof.save()
        return proof

    @action(detail=True, methods=["post"], url_path="pod-request")
    def pod_request(self, request, pk=None):
        """Issue the delivery OTP the consignee will quote back to the driver.

        The code is returned so the console can read it out or an SMS gateway can
        forward it; only signed-in staff can reach this endpoint.
        """
        order = self.get_object()
        if order.status == "cancelled":
            raise ValidationError("A cancelled consignment has nothing to deliver.")
        proof = self._open_proof(order)
        if proof is None:
            proof = ProofOfDelivery.objects.create(
                order=order, waypoint_id=request.data.get("waypoint") or None,
                proof_type=request.data.get("proof_type", "signature"),
                receiver_name=request.data.get("receiver_name", ""),
                receiver_phone=request.data.get("receiver_phone", ""))
        otp = proof.issue_otp()
        order.log(order.status, "POD_OTP_ISSUED",
                  f"Delivery OTP sent to {proof.receiver_phone or 'the consignee'}", city=order.dropoff.city)
        return Response({"proof": ProofOfDeliverySerializer(proof).data, "otp": otp,
                         "valid_hours": int(ProofOfDelivery.OTP_VALID_FOR.total_seconds() // 3600)})

    @action(detail=True, methods=["post"], url_path="pod-submit")
    @transaction.atomic
    def pod_submit(self, request, pk=None):
        """What the driver captures at the drop: receiver, OTP, signature, shortage, damage."""
        order = self.get_object()
        if not request.data.get("receiver_name") and not request.data.get("file_url"):
            raise ValidationError("Record who took delivery, or attach the signed POD.")
        proof = self._open_proof(order) or ProofOfDelivery.objects.create(order=order)
        self._capture(proof, request.data)
        order.log(order.status, "POD_CAPTURED",
                  f"Received by {proof.receiver_name or 'consignee'}"
                  + (f" · short {proof.shortage_kg} kg" if proof.shortage_kg else "")
                  + (" · damage reported" if proof.damage_reported else ""),
                  proof.latitude, proof.longitude, order.dropoff.city)
        return Response(ProofOfDeliverySerializer(proof).data, status=http_status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def complete(self, request, pk=None):
        """Close the order. A consignment that needs proof cannot close without it."""
        order = self.get_object()
        proof = None
        if order.pod_required:
            # Delivering straight from the board captures the ePOD in the same call.
            if any(request.data.get(field) for field in ("receiver_name", "file_url", "otp")):
                proof = self._capture(self._open_proof(order) or ProofOfDelivery.objects.create(order=order), request.data)
            proof = proof or order.proofs.filter(captured_at__isnull=False).order_by("-created_at").first()
            if proof is None:
                raise ValidationError("This consignment needs an ePOD. Capture who took delivery before completing it.")
        order.status = "completed"
        order.completed_at = timezone.now()
        order.save()
        order.waypoints.filter(status="pending").update(status="completed", actual_arrival=timezone.now())
        if order.vehicle_id:
            Vehicle.objects.filter(pk=order.vehicle_id).update(status="available")
        if order.driver_id:
            Driver.objects.filter(pk=order.driver_id).update(status="available")
        order.log("completed", "ORDER_COMPLETED", proof.receiver_name if proof else "Delivered", city=order.dropoff.city)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        order.status = "cancelled"
        order.save(update_fields=["status", "updated_at"])
        order.log("cancelled", "ORDER_CANCELLED", request.data.get("reason", ""))
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def reprice(self, request, pk=None):
        order = self.get_object()
        breakdown = order.price_from_rate_card()
        if breakdown is None:
            raise ValidationError("Link a rate card to this order before repricing.")
        return Response({"order": self.get_serializer(order).data, "breakdown": breakdown})

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def invoice(self, request, pk=None):
        """Bill the consignment. Freight, GST and the total all come from the rate card,
        and the invoice is posted to the ledger in the same step."""
        order = self.get_object()
        try:
            invoice, created = build_invoice_from_order(
                order, due_days=request.data.get("due_days", 15),
                additional_charges=request.data.get("additional_charges"))
        except BillingError as error:
            raise ValidationError(str(error))
        ledger, ledger_error = None, ""
        if request.data.get("post_to_ledger", True):
            try:
                entry = post_customer_invoice(invoice, branch=order.branch, created_by=request.user.get_username())
                ledger = {"number": entry.number, "id": entry.pk}
            except PostingError as error:
                ledger_error = str(error)
        return Response({"invoice": InvoiceSerializer(invoice).data, "created": created,
                         "journal_entry": ledger, "ledger_error": ledger_error,
                         "order": self.get_serializer(order).data},
                        status=http_status.HTTP_201_CREATED if created else http_status.HTTP_200_OK)


class WaypointViewSet(FilterableViewSet):
    queryset = Waypoint.objects.select_related("order", "place").all()
    serializer_class = WaypointSerializer
    filter_fields = ["order", "status", "waypoint_type"]

    @action(detail=True, methods=["post"])
    def arrive(self, request, pk=None):
        waypoint = self.get_object()
        waypoint.status = "completed"
        waypoint.actual_arrival = timezone.now()
        waypoint.save(update_fields=["status", "actual_arrival", "updated_at"])
        waypoint.order.log(waypoint.order.status, "WAYPOINT_REACHED", f"Reached {waypoint.place.name}", city=waypoint.place.city)
        return Response(self.get_serializer(waypoint).data)


class TrackingActivityViewSet(FilterableViewSet):
    queryset = TrackingActivity.objects.select_related("order").all()
    serializer_class = TrackingActivitySerializer
    filter_fields = ["order", "code", "status"]


class ProofOfDeliveryViewSet(FilterableViewSet):
    queryset = ProofOfDelivery.objects.select_related("order", "order__customer", "order__dropoff").all()
    serializer_class = ProofOfDeliverySerializer
    filter_fields = ["order", "proof_type", "status", "damage_reported"]
    search_fields = ["receiver_name", "receiver_phone", "order__number", "order__tracking_number"]

    @action(detail=False, methods=["get"])
    def pending(self, request):
        """The office review queue: captures held back by a shortage or damage."""
        records = self.get_queryset().filter(status="submitted")
        return Response({"count": records.count(), "proofs": self.get_serializer(records, many=True).data})

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        """Clear a capture for billing."""
        proof = self.get_object()
        if not proof.captured_at:
            raise ValidationError("Nothing has been captured against this proof yet.")
        proof.status = "verified"
        proof.verified_at = timezone.now()
        proof.verified_by = request.data.get("verified_by") or request.user.get_username()
        proof.rejection_reason = ""
        proof.save(update_fields=["status", "verified_at", "verified_by", "rejection_reason", "updated_at"])
        proof.order.log(proof.order.status, "POD_VERIFIED", f"ePOD cleared by {proof.verified_by}")
        return Response(self.get_serializer(proof).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Send a capture back to the driver, with the reason recorded against it."""
        proof = self.get_object()
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError("Say why the ePOD is being rejected so the driver can correct it.")
        proof.status = "rejected"
        proof.rejection_reason = reason[:240]
        proof.verified_at = None
        proof.verified_by = ""
        proof.save(update_fields=["status", "rejection_reason", "verified_at", "verified_by", "updated_at"])
        proof.order.log(proof.order.status, "POD_REJECTED", reason[:240])
        return Response(self.get_serializer(proof).data)


class FuelEntryViewSet(FilterableViewSet):
    required_permission = "expenses.view"; required_write_permission = "expenses.manage"
    queryset = FuelEntry.objects.select_related("vehicle", "driver", "trip").all()
    serializer_class = FuelEntrySerializer
    filter_fields = ["vehicle", "driver", "trip", "payment_method"]
    search_fields = ["station_name", "invoice_number"]

    @action(detail=False, methods=["get"])
    def mileage(self, request):
        """Average mileage and diesel spend per vehicle."""
        rows = (self.get_queryset().values("vehicle__registration_number")
                .annotate(fills=Count("id"), litres=Sum("volume_litres"), spend=Sum("amount"),
                          mileage=Avg("mileage_kmpl", filter=Q(mileage_kmpl__gt=0)))
                .order_by("-spend"))
        return Response([{ "vehicle": r["vehicle__registration_number"], "fills": r["fills"],
                           "litres": float(r["litres"] or 0), "spend": float(r["spend"] or 0),
                           "average_kmpl": float(round(r["mileage"] or 0, 2))} for r in rows])


class TripExpenseViewSet(FilterableViewSet):
    required_permission = "expenses.view"; required_write_permission = "expenses.manage"
    queryset = TripExpense.objects.select_related("trip", "vehicle", "driver", "vendor").all()
    serializer_class = TripExpenseSerializer
    filter_fields = ["trip", "order", "vehicle", "driver", "category", "status", "paid_by"]
    search_fields = ["receipt_number", "remarks"]

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        expense = self.get_object()
        expense.status = "approved"
        expense.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(expense).data)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        rows = self.get_queryset().values("category").annotate(total=Sum("amount"), entries=Count("id")).order_by("-total")
        return Response([{"category": r["category"], "total": float(r["total"] or 0), "entries": r["entries"]} for r in rows])


class IssueViewSet(FilterableViewSet):
    queryset = Issue.objects.select_related("vehicle", "driver", "trip", "order").all()
    serializer_class = IssueSerializer
    filter_fields = ["status", "priority", "issue_type", "vehicle", "driver"]
    search_fields = ["number", "description", "location_text"]

    def perform_create(self, serializer):
        serializer.save(number=serializer.validated_data.get("number") or "ISS-" + timezone.now().strftime("%y%m%d") + uuid4().hex[:6].upper())


    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        issue = self.get_object()
        issue.status = "resolved"
        issue.resolution = request.data.get("resolution", "")
        issue.resolved_at = timezone.now()
        issue.save(update_fields=["status", "resolution", "resolved_at", "updated_at"])
        return Response(self.get_serializer(issue).data)


class ComplianceDocumentViewSet(FilterableViewSet):
    required_permission = "compliance.view"; required_write_permission = "compliance.manage"
    queryset = ComplianceDocument.objects.select_related("vehicle", "driver").all()
    serializer_class = ComplianceDocumentSerializer
    filter_fields = ["vehicle", "driver", "document_type"]
    search_fields = ["number", "issuing_authority"]

    @action(detail=False, methods=["get"])
    def expiring(self, request):
        """Documents already expired or expiring within `days` (default 30)."""
        try:
            days = int(request.query_params.get("days", 30))
        except ValueError:
            raise ValidationError("`days` must be a whole number.")
        cutoff = timezone.localdate() + timedelta(days=days)
        records = self.get_queryset().filter(expiry_date__isnull=False, expiry_date__lte=cutoff).order_by("expiry_date")
        return Response({"days": days, "count": records.count(), "documents": self.get_serializer(records, many=True).data})


class MaintenanceScheduleViewSet(FilterableViewSet):
    required_permission = "maintenance.view"; required_write_permission = "maintenance.manage"
    queryset = MaintenanceSchedule.objects.select_related("vehicle").all()
    serializer_class = MaintenanceScheduleSerializer
    filter_fields = ["vehicle", "status"]
    search_fields = ["task"]

    @action(detail=False, methods=["get"])
    def due(self, request):
        records = [s for s in self.get_queryset() if s.is_due]
        return Response({"count": len(records), "schedules": self.get_serializer(records, many=True).data})

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """Record a service and roll the schedule forward."""
        schedule = self.get_object()
        schedule.last_service_km = int(request.data.get("odometer_km") or schedule.vehicle.current_odometer_km)
        schedule.last_service_date = request.data.get("service_date") or timezone.localdate()
        schedule.save()
        return Response(self.get_serializer(schedule).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def public_tracking(request, tracking_number):
    """Consignee facing consignment tracking, no login required."""
    order = Order.objects.filter(tracking_number__iexact=tracking_number).select_related("customer", "pickup", "dropoff").prefetch_related("activities").first()
    if not order:
        return Response({"detail": "No consignment found for this tracking number."}, status=http_status.HTTP_404_NOT_FOUND)
    return Response(PublicOrderTrackingSerializer(order).data)


@requires("reports.view")
@api_view(["GET"])
def fleet_analytics(request):
    """Operating KPIs an Indian fleet owner reviews daily."""
    today = timezone.localdate()
    period_start = today - timedelta(days=int(request.query_params.get("days", 30) or 30))
    fuel = FuelEntry.objects.filter(entry_date__gte=period_start)
    expenses = TripExpense.objects.filter(expense_date__gte=period_start)
    fuel_spend = fuel.aggregate(value=Sum("amount"))["value"] or Decimal("0")
    expense_spend = expenses.aggregate(value=Sum("amount"))["value"] or Decimal("0")
    litres = fuel.aggregate(value=Sum("volume_litres"))["value"] or Decimal("0")
    avg_mileage = fuel.exclude(mileage_kmpl=0).aggregate(value=Avg("mileage_kmpl"))["value"] or Decimal("0")
    km_run = money(litres * Decimal(str(round(float(avg_mileage), 2))))
    vehicles = Vehicle.objects.count()
    orders = Order.objects.filter(created_at__date__gte=period_start)
    completed = orders.filter(status="completed")
    revenue = orders.aggregate(value=Sum("total_amount"))["value"] or Decimal("0")
    return Response({
        "period_days": (today - period_start).days,
        "fleet_size": vehicles,
        "vehicles_on_trip": Vehicle.objects.filter(status="on_trip").count(),
        "utilisation_percent": round(Vehicle.objects.filter(status="on_trip").count() / max(vehicles, 1) * 100, 1),
        "orders": orders.count(),
        "orders_completed": completed.count(),
        "on_time_percent": round(completed.filter(scheduled_at__isnull=False, completed_at__lte=F("scheduled_at")).count() / max(completed.count(), 1) * 100, 1),
        "order_revenue": float(revenue),
        "fuel_spend": float(fuel_spend),
        "average_mileage_kmpl": float(round(avg_mileage, 2)),
        "estimated_km_run": float(km_run),
        "cost_per_km": float(money((fuel_spend + expense_spend) / (km_run or Decimal("1")))),
        "trip_expenses": float(expense_spend),
        "expense_split": [{"category": r["category"], "total": float(r["total"] or 0)}
                          for r in expenses.values("category").annotate(total=Sum("amount")).order_by("-total")],
        "open_issues": Issue.objects.exclude(status="resolved").count(),
        "documents_expiring": ComplianceDocument.objects.filter(expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=30)).count(),
        "maintenance_due": len([s for s in MaintenanceSchedule.objects.select_related("vehicle") if s.is_due]),
    })


# --- operations flow: indent -> allocation -> order -------------------------
from uuid import uuid4 as _uuid4

from .models import Indent
from .serializers import IndentSerializer


class IndentViewSet(FilterableViewSet):
    """Customer demand captured before a truck is committed to it."""
    queryset = Indent.objects.select_related("customer", "pickup", "dropoff", "branch", "vehicle", "driver", "order").all()
    serializer_class = IndentSerializer
    filter_fields = ["status", "customer", "branch", "vehicle", "service_rate"]
    search_fields = ["number", "material", "vehicle_type", "remarks"]

    def perform_create(self, serializer):
        serializer.save(number=serializer.validated_data.get("number")
                        or "IND-" + timezone.now().strftime("%y%m%d") + _uuid4().hex[:6].upper())

    @action(detail=True, methods=["post"])
    def allocate(self, request, pk=None):
        """Commit a vehicle and driver to the indent."""
        indent = self.get_object()
        if indent.status in ("converted", "cancelled"):
            raise ValidationError(f"An indent that is {indent.status} cannot be allocated.")
        vehicle_id, driver_id = request.data.get("vehicle"), request.data.get("driver")
        if not vehicle_id or not driver_id:
            raise ValidationError("Both a vehicle and a driver are required to allocate an indent.")
        indent.vehicle = Vehicle.objects.get(pk=vehicle_id)
        indent.driver = Driver.objects.get(pk=driver_id)
        indent.status = "allocated"
        indent.save(update_fields=["vehicle", "driver", "status", "updated_at"])
        return Response(self.get_serializer(indent).data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def convert(self, request, pk=None):
        """Turn an allocated indent into a live consignment order."""
        indent = self.get_object()
        if indent.order_id:
            raise ValidationError(f"This indent already became order {indent.order.number}.")
        if indent.status != "allocated":
            raise ValidationError("Allocate a vehicle and driver before converting the indent.")
        order = Order.objects.create(
            number="ORD-" + timezone.now().strftime("%y%m%d") + _uuid4().hex[:6].upper(),
            customer=indent.customer, branch=indent.branch, pickup=indent.pickup, dropoff=indent.dropoff,
            service_rate=indent.service_rate, vehicle=indent.vehicle, driver=indent.driver,
            payload_description=indent.material, weight_kg=indent.weight_kg,
            scheduled_at=indent.required_at, status="assigned")
        coordinates = [order.pickup.latitude, order.pickup.longitude, order.dropoff.latitude, order.dropoff.longitude]
        if all(value is not None for value in coordinates):
            order.distance_km = haversine_km(order.pickup.latitude, order.pickup.longitude,
                                             order.dropoff.latitude, order.dropoff.longitude)
            order.save(update_fields=["distance_km"])
        if order.service_rate:
            order.price_from_rate_card()
        order.log("assigned", "ORDER_FROM_INDENT", f"Converted from indent {indent.number}", city=order.pickup.city)
        indent.order = order
        indent.status = "converted"
        indent.save(update_fields=["order", "status", "updated_at"])
        return Response({"indent": self.get_serializer(indent).data, "order": OrderSerializer(order).data})

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        indent = self.get_object()
        indent.status = "cancelled"
        indent.remarks = request.data.get("reason", indent.remarks)
        indent.save(update_fields=["status", "remarks", "updated_at"])
        return Response(self.get_serializer(indent).data)


@requires("reports.view")
@api_view(["GET"])
def order_profitability(request, pk):
    """Revenue, diesel and on-road cost for one consignment."""
    order = Order.objects.filter(pk=pk).select_related("vehicle").first()
    if not order:
        return Response({"detail": "Order not found."}, status=404)
    expenses = TripExpense.objects.filter(order=order).aggregate(value=Sum("amount"))["value"] or 0
    fuel = FuelEntry.objects.filter(trip=order.trip).aggregate(value=Sum("amount"))["value"] or 0 if order.trip_id else 0
    revenue = money(order.total_amount)
    cost = money(expenses) + money(fuel)
    profit = money(revenue - cost)
    return Response({
        "order": order.number, "vehicle": getattr(order.vehicle, "registration_number", ""),
        "revenue": float(revenue), "trip_expenses": float(money(expenses)), "fuel": float(money(fuel)),
        "total_cost": float(cost), "profit": float(profit),
        "margin_percent": float(round(profit / revenue * 100, 2)) if revenue else 0.0,
        "cost_per_km": float(money(cost / order.distance_km)) if order.distance_km else 0.0,
    })
