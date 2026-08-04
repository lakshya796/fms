from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.decorators import permission_classes
from .models import Customer, Driver, Vehicle, LorryReceipt, Trip, TrackingEvent, Invoice, Settlement, SalesQuote
from .serializers import CustomerSerializer, DriverSerializer, VehicleSerializer, LorryReceiptSerializer, TripSerializer, TrackingEventSerializer, InvoiceSerializer, SettlementSerializer, SalesQuoteSerializer

@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"status": "ok", "service": "phloz-fms-api", "time": timezone.now()})

class SalesQuoteViewSet(viewsets.ModelViewSet):
    queryset = SalesQuote.objects.select_related("customer").all().order_by("-created_at")
    serializer_class = SalesQuoteSerializer
class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all().order_by("-created_at"); serializer_class = CustomerSerializer
class DriverViewSet(viewsets.ModelViewSet):
    queryset = Driver.objects.all().order_by("name"); serializer_class = DriverSerializer
class VehicleViewSet(viewsets.ModelViewSet):
    queryset = Vehicle.objects.all().order_by("registration_number"); serializer_class = VehicleSerializer
class LorryReceiptViewSet(viewsets.ModelViewSet):
    queryset = LorryReceipt.objects.select_related("customer").all().order_by("-created_at"); serializer_class = LorryReceiptSerializer
class TrackingEventViewSet(viewsets.ModelViewSet):
    queryset = TrackingEvent.objects.select_related("trip").all().order_by("-recorded_at"); serializer_class = TrackingEventSerializer
class TripViewSet(viewsets.ModelViewSet):
    queryset = Trip.objects.select_related("vehicle", "driver").prefetch_related("lorry_receipts", "tracking_events").all().order_by("-created_at")
    serializer_class = TripSerializer
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def dispatch(self, request, pk=None):
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
    queryset = Invoice.objects.select_related("customer", "trip").all().order_by("-created_at"); serializer_class = InvoiceSerializer
class SettlementViewSet(viewsets.ModelViewSet):
    queryset = Settlement.objects.select_related("driver", "trip").all().order_by("-created_at"); serializer_class = SettlementSerializer
