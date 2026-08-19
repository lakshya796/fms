import json as _json
from decimal import Decimal

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
from . import geotrackers
from .models import (Customer, Driver, Vehicle, LorryReceipt, Trip, TrackingEvent, Invoice, Settlement, SalesQuote,
                     MaintenanceWorkOrder, VEHICLE_STATUSES, set_vehicle_status)
from .serializers import (CustomerSerializer, DriverSerializer, VehicleSerializer, VehicleStatusLogSerializer,
                          LorryReceiptSerializer, TripSerializer, TripSettlementInputSerializer, TrackingEventSerializer,
                          InvoiceSerializer, SettlementSerializer, SalesQuoteSerializer, MaintenanceWorkOrderSerializer)

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

    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        """Manual status change for states nothing else drives automatically -
        breakdown, workshop, driver unavailable, idle, and so on."""
        vehicle = self.get_object()
        status_value = request.data.get("status")
        if status_value not in dict(VEHICLE_STATUSES):
            raise ValidationError(f"status must be one of {[code for code, _ in VEHICLE_STATUSES]}")
        set_vehicle_status(vehicle, status_value, reason=request.data.get("reason", ""),
                           changed_by=request.user.get_username(),
                           latitude=request.data.get("latitude"), longitude=request.data.get("longitude"))
        return Response(self.get_serializer(vehicle).data)

    @action(detail=True, methods=["get"], url_path="status-history")
    def status_history(self, request, pk=None):
        vehicle = self.get_object()
        records = vehicle.status_log.select_related("place", "trip").all()[:200]
        return Response(VehicleStatusLogSerializer(records, many=True).data)

    @action(detail=False, methods=["get"])
    def availability(self, request):
        """Available now, and available shortly nearby - who can take an order at
        `place`, ranked by distance, with a document-validity flag so a dispatcher
        does not allocate a truck whose papers have lapsed.
        """
        place = None
        place_id = request.query_params.get("place")
        if place_id:
            try:
                place = Place.objects.get(pk=place_id)
            except Place.DoesNotExist:
                raise ValidationError("Unknown place.")
        radius_km = request.query_params.get("radius_km")
        queryset = self.get_queryset().select_related("current_place", "current_trip", "vendor")
        status_filter = request.query_params.get("status")
        queryset = queryset.filter(status=status_filter) if status_filter else queryset.filter(status__in=["available", "idle", "running", "allocated"])
        today = timezone.localdate()
        results = []
        for vehicle in queryset:
            distance = None
            if place is not None and place.latitude is not None and vehicle.current_latitude is not None:
                distance = haversine_km(vehicle.current_latitude, vehicle.current_longitude, place.latitude, place.longitude)
            if radius_km is not None and (distance is None or distance > float(radius_km)):
                continue
            expired_documents = ComplianceDocument.objects.filter(vehicle=vehicle, expiry_date__isnull=False, expiry_date__lt=today).count()
            results.append({
                "id": vehicle.pk, "registration_number": vehicle.registration_number, "vehicle_type": vehicle.vehicle_type,
                "capacity_kg": vehicle.capacity_kg, "ownership": vehicle.ownership, "status": vehicle.status,
                "current_place": getattr(vehicle.current_place, "name", ""), "current_trip": getattr(vehicle.current_trip, "number", ""),
                "distance_km": distance, "available_now": vehicle.status in ("available", "idle"),
                "expected_available_at": vehicle.expected_available_at, "expired_documents": expired_documents,
            })
        results.sort(key=lambda row: (row["distance_km"] is None, row["distance_km"] or 0))
        return Response({"place": place.name if place else None, "count": len(results), "vehicles": results})
class LorryReceiptViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "operations.view"; required_write_permission = "operations.manage"
    queryset = LorryReceipt.objects.select_related("customer").all().order_by("-created_at"); serializer_class = LorryReceiptSerializer

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """The consignment note PDF - the document a driver carries and a
        consignee signs, generated the same way the invoice PDF is."""
        from io import BytesIO
        from django.http import HttpResponse
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_RIGHT
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable

        lr = self.get_object()
        order = lr.orders.first()

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=15*mm, leftMargin=15*mm, topMargin=15*mm, bottomMargin=15*mm)

        brand = colors.HexColor("#0d5f45")
        light = colors.HexColor("#f0f4f2")
        mid = colors.HexColor("#666666")

        def ps(name, **kw): return ParagraphStyle(name, **kw)
        h1 = ps("h1", fontSize=22, fontName="Helvetica-Bold", textColor=brand)
        h2 = ps("h2", fontSize=11, fontName="Helvetica-Bold")
        body = ps("body", fontSize=9, fontName="Helvetica")
        small = ps("small", fontSize=8, fontName="Helvetica", textColor=mid)
        r_bold = ps("r_bold", fontSize=10, fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=brand)
        lbl = ps("lbl", fontSize=7, fontName="Helvetica-Bold", textColor=mid)

        elems = []
        hdr = [
            [Paragraph("PHLOZ FMS", h1),
             Paragraph("LORRY RECEIPT", ps("lr", fontSize=16, fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=brand))],
            [Paragraph("Fleet Management System", small),
             Paragraph(f"<b>{lr.number}</b>", ps("lrno", fontSize=11, fontName="Helvetica-Bold", alignment=TA_RIGHT))],
            [Paragraph("", body),
             Paragraph(f"Date: {lr.created_at.strftime('%d %b %Y')}", ps("d", fontSize=9, alignment=TA_RIGHT))],
        ]
        hdr_t = Table(hdr, colWidths=[100*mm, 75*mm])
        hdr_t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LINEBELOW", (0, -1), (-1, -1), 2, brand), ("BOTTOMPADDING", (0, -1), (-1, -1), 6)]))
        elems.append(hdr_t)
        elems.append(Spacer(1, 5*mm))

        parties = [
            [Paragraph("CONSIGNOR", lbl), Paragraph("CONSIGNEE", lbl)],
            [Paragraph(f"<b>{lr.consignor}</b>", h2), Paragraph(f"<b>{lr.consignee}</b>", h2)],
            [Paragraph(f"From: {lr.origin}", small), Paragraph(f"To: {lr.destination}", small)],
        ]
        parties_t = Table(parties, colWidths=[87.5*mm, 87.5*mm])
        parties_t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, 0), light),
                                       ("TOPPADDING", (0, 0), (-1, 0), 3), ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
                                       ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#cccccc"))]))
        elems.append(parties_t)
        elems.append(Spacer(1, 4*mm))

        if order:
            ref = [
                [Paragraph("CONSIGNMENT", lbl), Paragraph("VEHICLE", lbl), Paragraph("E-WAY BILL", lbl)],
                [Paragraph(f"<b>{order.number}</b>", h2),
                 Paragraph(order.vehicle.registration_number if order.vehicle else "—", body),
                 Paragraph(lr.eway_bill_number or "—", body)],
            ]
            ref_t = Table(ref, colWidths=[60*mm, 60*mm, 55*mm])
            ref_t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, 0), light),
                                       ("TOPPADDING", (0, 0), (-1, 0), 3), ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
                                       ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#cccccc"))]))
            elems.append(ref_t)
            elems.append(Spacer(1, 4*mm))

        rows = [
            [Paragraph("Particulars", ps("th", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)),
             Paragraph("Detail", ps("th_r", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT))],
            ["Material", Paragraph(lr.material, ps("r", fontSize=9, alignment=TA_RIGHT))],
            ["Packages", Paragraph(str(lr.packages), ps("r2", fontSize=9, alignment=TA_RIGHT))],
            ["Weight", Paragraph(f"{float(lr.weight_kg):,.0f} kg", ps("r3", fontSize=9, alignment=TA_RIGHT))],
            [Paragraph("FREIGHT", h2), Paragraph(f"₹ {float(lr.freight_amount):,.2f}", r_bold)],
        ]
        table = Table(rows, colWidths=[130*mm, 45*mm])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BACKGROUND", (0, 0), (-1, 0), brand),
            ("LINEABOVE", (0, -1), (-1, -1), 1.5, brand), ("BACKGROUND", (0, -1), (-1, -1), light),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#fafafa")]),
            ("PADDING", (0, 0), (-1, -1), 7), ("LINEBELOW", (0, 1), (-1, -3), 0.3, colors.HexColor("#e0e0e0")),
        ]))
        elems.append(table)
        elems.append(Spacer(1, 6*mm))
        elems.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
        elems.append(Spacer(1, 3*mm))
        elems.append(Paragraph(
            "Goods carried at owner's risk. Subject to the terms and conditions of carriage. "
            "This is a computer-generated document and requires no signature to be valid for despatch.", small))

        doc.build(elems)
        buffer.seek(0)
        response = HttpResponse(buffer.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{lr.number}.pdf"'
        return response
class TrackingEventViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "operations.view"; required_write_permission = "operations.manage"
    queryset = TrackingEvent.objects.select_related("trip").all().order_by("-recorded_at"); serializer_class = TrackingEventSerializer
class TripViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "operations.view"; required_write_permission = "operations.manage"
    queryset = Trip.objects.select_related("vehicle", "driver").prefetch_related(
        "lorry_receipts", "tracking_events",
        "orders", "orders__customer", "orders__pickup", "orders__dropoff",
    ).all().order_by("-created_at")
    serializer_class = TripSerializer

    @action(detail=True, methods=["post"], url_path="add-order")
    @transaction.atomic
    def add_order(self, request, pk=None):
        """Link an additional order to this trip (milk-run support)."""
        from .models import Order as _Order
        from rest_framework.exceptions import ValidationError as _VE
        trip = self.get_object()
        order_id = request.data.get("order")
        if not order_id:
            raise _VE("Provide an order id.")
        try:
            order = _Order.objects.select_related("trip").get(pk=order_id)
        except _Order.DoesNotExist:
            raise _VE("Order not found.")
        if order.trip_id and order.trip_id != trip.pk:
            raise _VE(f"Order {order.number} is already on trip {order.trip.number}. Remove it first.")
        order.trip = trip
        order.save(update_fields=["trip", "updated_at"])
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="remove-order")
    @transaction.atomic
    def remove_order(self, request, pk=None):
        """Unlink an order from this trip."""
        from .models import Order as _Order
        trip = self.get_object()
        _Order.objects.filter(pk=request.data.get("order"), trip=trip).update(trip=None)
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"], url_path="dispatch")
    @transaction.atomic
    def dispatch_trip(self, request, pk=None):
        trip = self.get_object()
        trip.status = "dispatched"; trip.actual_departure = timezone.now(); trip.save()
        set_vehicle_status(trip.vehicle, "running", trip=trip, reason="Trip dispatched")
        trip.driver.status = "on_trip"; trip.driver.save(update_fields=["status"])
        # An LR is a legal precondition for the goods moving - issue one now for
        # every order on the trip that does not already have one, rather than
        # leaving it to an operator to remember (docs/ONE-TRIP-END-TO-END.md §5 Phase 1).
        for order in trip.orders.filter(lorry_receipt__isnull=True):
            build_lr_from_order(order)
        trip.lorry_receipts.update(status="dispatched")
        return Response(self.get_serializer(trip).data)
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def close(self, request, pk=None):
        trip = self.get_object()
        trip.status = "closed"; trip.arrival_at = trip.arrival_at or timezone.now(); trip.save()
        set_vehicle_status(trip.vehicle, "available", trip=None, reason="Trip closed")
        trip.driver.status = "available"; trip.driver.save(update_fields=["status"])
        trip.lorry_receipts.update(status="delivered")
        return Response(self.get_serializer(trip).data)

    @action(detail=True, methods=["get", "post"])
    @transaction.atomic
    def settlement(self, request, pk=None):
        """The trip sheet a transport office fills in by hand: load type, dates,
        distance, odometer, freight and every expense line item, in one submission.

        GET returns the computed summary against whatever is on file. POST updates
        the trip's own fields and upserts one TripExpense per category in the
        `expenses` map - a category omitted or zeroed is cleared, so re-submitting
        the form (e.g. after a correction) never leaves a stale duplicate line behind.
        """
        trip = self.get_object()

        def expense_breakdown():
            rows = trip.expenses.values("category").annotate(total=Sum("amount"))
            return {row["category"]: float(row["total"]) for row in rows}

        if request.method == "GET":
            return Response({"trip": self.get_serializer(trip).data, "summary": trip.settlement_summary(),
                             "expenses": expense_breakdown()})

        form = TripSettlementInputSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data

        trip_fields = ["load_type", "load_date", "unload_date", "google_km", "passed_km",
                       "start_odometer_km", "end_odometer_km", "freight_amount"]
        changed = []
        for field in trip_fields:
            if field in data:
                setattr(trip, field, data[field])
                changed.append(field)
        if "diesel_given" in data:
            trip.advance_amount = data["diesel_given"]
            changed.append("advance_amount")
        if changed:
            trip.save(update_fields=changed + ["updated_at"])

        if trip.end_odometer_km and trip.end_odometer_km > trip.vehicle.current_odometer_km:
            Vehicle.objects.filter(pk=trip.vehicle_id).update(current_odometer_km=trip.end_odometer_km)

        for category, amount in (data.get("expenses") or {}).items():
            if amount:
                TripExpense.objects.update_or_create(
                    trip=trip, category=category,
                    defaults={"amount": amount, "vehicle": trip.vehicle, "driver": trip.driver, "status": "approved"})
            else:
                TripExpense.objects.filter(trip=trip, category=category).delete()

        trip.refresh_from_db()
        return Response({"trip": self.get_serializer(trip).data, "summary": trip.settlement_summary(),
                         "expenses": expense_breakdown()})

    @action(detail=True, methods=["get"])
    def cockpit(self, request, pk=None):
        """One trip's whole stack - every order it carries with its LR, POD and
        invoice state, the apportioned cost behind each one, and what still
        stands between this trip and being closed out - instead of the eight
        screens it otherwise takes to piece the same picture together by hand.
        See docs/ONE-TRIP-END-TO-END.md §5 Phase 3.
        """
        trip = self.get_object()
        orders = list(trip.orders.select_related("customer", "pickup", "dropoff", "lorry_receipt")
                                 .prefetch_related("proofs", "invoices").order_by("id"))
        split = apportion_trip_cost(trip)

        order_rows = []
        documents = []
        blockers = []
        unpriced, unposted_lr, unverified_pod, unbilled = 0, 0, 0, 0
        for order in orders:
            lr = order.lorry_receipt
            invoice = order_invoice(order)
            proofs = list(order.proofs.all())
            captured = any(p.captured_at for p in proofs)
            verified = any(p.status == "verified" for p in proofs)
            pending_proof = next((p for p in proofs if p.status == "submitted"), None)
            cost_row = split["orders"].get(order.id, {"fuel": Decimal("0"), "shared_expenses": Decimal("0"),
                                                       "attributed_expenses": Decimal("0"), "total_cost": Decimal("0")})
            revenue = money(order.total_amount)
            order_rows.append({
                "id": order.id, "number": order.number, "status": order.status,
                "customer": order.customer.name, "route": f"{order.pickup.city} → {order.dropoff.city}",
                "weight_kg": float(order.weight_kg or 0), "distance_km": float(order.distance_km or 0),
                "revenue": float(revenue),
                "cost": {"fuel": float(cost_row["fuel"]), "shared_expenses": float(cost_row["shared_expenses"]),
                        "attributed_expenses": float(cost_row["attributed_expenses"]),
                        "total": float(cost_row["total_cost"])},
                "profit": float(money(revenue - cost_row["total_cost"])),
                "lorry_receipt": {"id": lr.id, "number": lr.number, "status": lr.status} if lr else None,
                "pod": {"captured": captured, "verified": verified, "required": order.pod_required,
                       "pending_proof_id": pending_proof.id if pending_proof else None},
                "invoice": {"id": invoice.id, "number": invoice.number, "status": invoice.status,
                           "total_amount": float(invoice.total_amount)} if invoice else None,
            })
            if lr:
                documents.append({"type": "lorry_receipt", "label": lr.number, "order": order.number,
                                  "url": f"lorry-receipts/{lr.id}/pdf/"})
            else:
                unposted_lr += 1
            for proof in order.proofs.all():
                if proof.file_url:
                    documents.append({"type": "pod", "label": f"POD · {order.number}", "order": order.number,
                                      "url": proof.file_url})
            if invoice:
                documents.append({"type": "invoice", "label": invoice.number, "order": order.number,
                                  "url": f"invoices/{invoice.id}/pdf/"})
            elif order.status == "completed":
                unbilled += 1
            if order.pod_required and not verified and order.status != "cancelled":
                unverified_pod += 1
            if not order.total_amount:
                unpriced += 1

        if unposted_lr:
            blockers.append(f"{unposted_lr} order(s) have no lorry receipt yet.")
        if unverified_pod:
            blockers.append(f"{unverified_pod} order(s) require a POD that has not been verified.")
        if unpriced:
            blockers.append(f"{unpriced} order(s) have no freight priced.")
        if unbilled:
            blockers.append(f"{unbilled} delivered order(s) have not been invoiced.")
        if trip.end_odometer_km is None:
            blockers.append("Trip has no end odometer reading.")
        unapproved = money(trip.expenses.filter(status="pending").aggregate(v=Sum("amount"))["v"] or 0)
        if unapproved:
            blockers.append(f"₹{unapproved:,.2f} of expenses are awaiting approval.")

        # The all-in figures - what the cockpit is for - summed from the same
        # per-order apportionment above, so fuel is always in the total cost.
        total_revenue = money(sum((row["revenue"] for row in order_rows), 0))
        total_cost = money(sum((row["cost"]["total"] for row in order_rows), 0))
        total_profit = money(total_revenue - total_cost)

        # trip.settlement_summary() is a narrower, pre-existing figure: the
        # driver's cash-advance reconciliation. Its `total_exp` is TripExpense
        # only - fuel is deliberately excluded there, since it may be paid on
        # a company fuel card rather than out of the driver's own advance -
        # so it is not the all-in P&L above and the two will not match.
        summary = trip.settlement_summary()
        summary["cost_basis"] = split["basis"]

        return Response({
            "trip": self.get_serializer(trip).data,
            "orders": order_rows,
            "costs": {"shared_fuel": float(split["shared_fuel"]), "shared_expenses": float(split["shared_expenses"]),
                     "shared_total": float(split["shared_total"]), "basis": split["basis"],
                     "advance_amount": float(money(trip.advance_amount)), "total_cost": float(total_cost)},
            "revenue": {"total": float(total_revenue)},
            "profit": {"total": float(total_profit)},
            "pnl": summary,
            "documents": documents,
            "blockers": blockers,
            "ready_to_close": not blockers,
        })

    @action(detail=True, methods=["post"], url_path="consolidate-invoice")
    @transaction.atomic
    def consolidate_invoice(self, request, pk=None):
        """One invoice per customer for every billable order on this trip,
        instead of one per consignment even when several ride the same truck
        for the same customer - see docs/ONE-TRIP-END-TO-END.md §5 Phase 5."""
        trip = self.get_object()
        invoices, skipped = build_invoice_from_trip(trip, due_days=request.data.get("due_days", 15))
        posted = []
        if request.data.get("post_to_ledger", True):
            for invoice, created in invoices:
                if not created:
                    continue
                try:
                    entry = post_customer_invoice(invoice, branch=trip.orders.first().branch if trip.orders.exists() else None,
                                                  created_by=request.user.get_username())
                    posted.append({"invoice": invoice.number, "journal_entry": entry.number})
                except PostingError as error:
                    posted.append({"invoice": invoice.number, "ledger_error": str(error)})
        return Response({
            "invoices": [InvoiceSerializer(invoice).data for invoice, _ in invoices],
            "skipped": [{"order": order.number, "reason": reason} for order, reason in skipped],
            "posted": posted,
        }, status=http_status.HTTP_201_CREATED if invoices else http_status.HTTP_200_OK)


class InvoiceViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "accounting.view"; required_write_permission = "accounting.manage"
    queryset = Invoice.objects.select_related("customer", "trip", "order", "order__pickup", "order__dropoff", "order__vehicle").all().order_by("-created_at")
    serializer_class = InvoiceSerializer

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """Generate a GST-compliant tax invoice PDF for download."""
        from io import BytesIO
        from django.http import HttpResponse
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_RIGHT, TA_CENTER
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable

        invoice = self.get_object()
        order = invoice.order
        customer = invoice.customer

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4,
                                rightMargin=15*mm, leftMargin=15*mm,
                                topMargin=15*mm, bottomMargin=15*mm)

        brand = colors.HexColor("#0d5f45")
        light = colors.HexColor("#f0f4f2")
        mid = colors.HexColor("#666666")

        def ps(name, **kw): return ParagraphStyle(name, **kw)

        h1 = ps("h1", fontSize=22, fontName="Helvetica-Bold", textColor=brand)
        h2 = ps("h2", fontSize=11, fontName="Helvetica-Bold")
        body = ps("body", fontSize=9, fontName="Helvetica")
        small = ps("small", fontSize=8, fontName="Helvetica", textColor=mid)
        r_body = ps("r_body", fontSize=9, fontName="Helvetica", alignment=TA_RIGHT)
        r_bold = ps("r_bold", fontSize=10, fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=brand)
        lbl = ps("lbl", fontSize=7, fontName="Helvetica-Bold", textColor=mid)

        def money_str(value):
            return f"{float(value or 0):,.2f}"

        elems = []

        # ---- Header ----
        hdr = [
            [Paragraph("PHLOZ FMS", h1),
             Paragraph(f"TAX INVOICE", ps("inv", fontSize=16, fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=brand))],
            [Paragraph("Fleet Management System", small),
             Paragraph(f"<b>{invoice.number}</b>", ps("invno", fontSize=11, fontName="Helvetica-Bold", alignment=TA_RIGHT))],
            [Paragraph("", body),
             Paragraph(f"Date: {invoice.created_at.strftime('%d %b %Y')}", ps("d", fontSize=9, alignment=TA_RIGHT))],
            [Paragraph("", body),
             Paragraph(f"Due: {invoice.due_date.strftime('%d %b %Y')}", ps("d2", fontSize=9, alignment=TA_RIGHT, textColor=mid))],
        ]
        hdr_t = Table(hdr, colWidths=[100*mm, 75*mm])
        hdr_t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, -1), (-1, -1), 2, brand),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 6),
        ]))
        elems.append(hdr_t)
        elems.append(Spacer(1, 5*mm))

        # ---- Bill-to / Supply ----
        bt = [
            [Paragraph("BILL TO", lbl), Paragraph("PLACE OF SUPPLY", lbl), Paragraph("STATUS", lbl)],
            [Paragraph(f"<b>{customer.name}</b>", h2), Paragraph(invoice.place_of_supply or "—", body),
             Paragraph(f"<b>{invoice.status.upper()}</b>", ps("st", fontSize=9, fontName="Helvetica-Bold", textColor=brand))],
            [Paragraph(f"GSTIN: {customer.gstin}", small),
             Paragraph("Reverse Charge: Yes" if invoice.reverse_charge else "", small), ""],
        ]
        bt_t = Table(bt, colWidths=[85*mm, 65*mm, 25*mm])
        bt_t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0), light),
            ("TOPPADDING", (0, 0), (-1, 0), 3),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
            ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ]))
        elems.append(bt_t)
        elems.append(Spacer(1, 4*mm))

        # ---- Consignment reference ----
        if order:
            ref = [
                [Paragraph("CONSIGNMENT", lbl), Paragraph("ROUTE", lbl), Paragraph("DISTANCE · WEIGHT", lbl)],
                [Paragraph(f"<b>{order.number}</b>", h2),
                 Paragraph(f"<b>{order.pickup.city} → {order.dropoff.city}</b>", h2),
                 Paragraph(f"{float(order.distance_km):.0f} km · {float(order.weight_kg):,.0f} kg", body)],
                [Paragraph(f"Tracking: {order.tracking_number}", small),
                 Paragraph(f"Vehicle: {order.vehicle.registration_number if order.vehicle else '—'}", small),
                 Paragraph(f"E-Way: {order.eway_bill_number or '—'}", small)],
            ]
            ref_t = Table(ref, colWidths=[60*mm, 75*mm, 40*mm])
            ref_t.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), light),
                ("TOPPADDING", (0, 0), (-1, 0), 3),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
                ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ]))
            elems.append(ref_t)
            elems.append(Spacer(1, 4*mm))

        # ---- Consolidated invoice: one row per consignment ----
        if invoice.line_items:
            item_rows = [[Paragraph("Consignment", ps("th2", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white)),
                         Paragraph("Route", ps("th3", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white)),
                         Paragraph("Amount (₹)", ps("th4", fontSize=8, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT))]]
            for line in invoice.line_items:
                item_rows.append([line.get("order_number", ""), line.get("route", ""),
                                  Paragraph(money_str(line.get("total_amount", 0)), r_body)])
            items_t = Table(item_rows, colWidths=[45*mm, 90*mm, 40*mm])
            items_t.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BACKGROUND", (0, 0), (-1, 0), mid),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
                ("PADDING", (0, 0), (-1, -1), 5), ("FONTSIZE", (0, 1), (-1, -1), 8),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e0e0e0")),
            ]))
            elems.append(Paragraph(f"CONSOLIDATED INVOICE — {len(invoice.line_items)} consignment(s)", lbl))
            elems.append(Spacer(1, 2*mm))
            elems.append(items_t)
            elems.append(Spacer(1, 4*mm))

        # ---- Charges ----
        rows = [
            [Paragraph("Particulars", ps("th", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)),
             Paragraph("Amount (₹)", ps("th_r", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT))],
            ["Freight", Paragraph(money_str(invoice.freight_amount), r_body)],
        ]
        if float(invoice.additional_charges):
            rows.append(["Additional charges", Paragraph(money_str(invoice.additional_charges), r_body)])

        taxable = float(invoice.freight_amount) + float(invoice.additional_charges)
        rows.append([Paragraph("Taxable value", h2), Paragraph(money_str(taxable), r_bold)])

        if invoice.reverse_charge:
            rows.append([Paragraph("GST (Reverse Charge — payable by recipient)", small), Paragraph("—", r_body)])
        else:
            rows.append([f"GST @ {invoice.gst_percent}%", Paragraph(money_str(invoice.tax_amount), r_body)])

        rows.append([Paragraph("INVOICE TOTAL", ps("total_l", fontSize=11, fontName="Helvetica-Bold")),
                     Paragraph(f"₹ {money_str(invoice.total_amount)}", r_bold)])

        charges_t = Table(rows, colWidths=[130*mm, 45*mm])
        charges_t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BACKGROUND", (0, 0), (-1, 0), brand),
            ("LINEABOVE", (0, -1), (-1, -1), 1.5, brand),
            ("BACKGROUND", (0, -1), (-1, -1), light),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#fafafa")]),
            ("PADDING", (0, 0), (-1, -1), 7),
            ("LINEBELOW", (0, 1), (-1, -3), 0.3, colors.HexColor("#e0e0e0")),
        ]))
        elems.append(charges_t)
        elems.append(Spacer(1, 6*mm))
        elems.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
        elems.append(Spacer(1, 3*mm))
        elems.append(Paragraph(
            f"Payment due by {invoice.due_date.strftime('%d %B %Y')}. "
            "This is a computer-generated document and requires no signature.",
            small))

        doc.build(elems)
        buffer.seek(0)
        response = HttpResponse(buffer.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{invoice.number}.pdf"'
        return response

class SettlementViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "accounting.view"; required_write_permission = "accounting.manage"
    queryset = Settlement.objects.select_related("driver", "trip").all().order_by("-created_at"); serializer_class = SettlementSerializer


# --- Fleetbase FleetOps inspired endpoints ---------------------------------
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4
from django.db.models import Avg, Count, F, Q
from rest_framework import status as http_status
from rest_framework.exceptions import ValidationError
from .models import (Vendor, ServiceArea, Zone, Place, Fleet, ServiceRate, ServiceQuote, Order, Waypoint,
                     TrackingActivity, ProofOfDelivery, FuelEntry, TripExpense, Issue, ComplianceDocument,
                     MaintenanceSchedule, VehicleHire, ORDER_STATUSES, haversine_km, money)
from .models import VendorLaneRate
from .serializers import (VendorSerializer, ServiceAreaSerializer, ZoneSerializer, PlaceSerializer, FleetSerializer,
                          ServiceRateSerializer, ServiceQuoteSerializer, OrderSerializer, WaypointSerializer,
                          TrackingActivitySerializer, ProofOfDeliverySerializer, PublicOrderTrackingSerializer,
                          FuelEntrySerializer, TripExpenseSerializer, IssueSerializer, ComplianceDocumentSerializer,
                          MaintenanceScheduleSerializer, VehicleHireSerializer, VendorLaneRateSerializer,
                          QuoteRequestSerializer, ProjectionRequestSerializer)
from .billing import BillingError, build_invoice_from_order, build_invoice_from_trip, order_invoice, order_pod_state, project_lane
from .costing import apportion_trip_cost
from .lr import build_lr_from_order
from .vendor_billing import HireBillingError, confirmation_email, raise_vendor_bill, vendor_payable
from .allocation import recommend_vehicles
from accounting.services import PostingError, post_customer_invoice
from iam import messaging as outbound_messaging


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
        trip = order.ensure_trip()
        set_vehicle_status(order.vehicle, "allocated", trip=trip, reason=f"Assigned to order {order.number}")
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
        trip = order.ensure_trip()
        if trip:
            trip.status = "dispatched"
            trip.actual_departure = order.dispatched_at
            trip.save(update_fields=["status", "actual_departure", "updated_at"])
        Driver.objects.filter(pk=order.driver_id).update(status="on_trip")
        set_vehicle_status(order.vehicle, "running", trip=trip, place=order.pickup, reason="Order dispatched")
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

    @action(detail=True, methods=["post"], url_path="pod-document-upload")
    def pod_document_upload(self, request, pk=None):
        """Upload a POD document (photo or PDF). Returns a URL to pass as file_url in pod-submit."""
        import os
        from pathlib import Path
        from django.conf import settings as _settings
        order = self.get_object()
        uploaded = request.FILES.get("document")
        if not uploaded:
            raise ValidationError("No file received. POST multipart/form-data with a 'document' field.")
        ext = os.path.splitext(uploaded.name)[1].lower() or ".bin"
        filename = f"pod-documents/{order.pk}_{uuid4().hex[:10]}{ext}"
        dest = Path(_settings.MEDIA_ROOT) / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in uploaded.chunks():
                f.write(chunk)
        url = request.build_absolute_uri(_settings.MEDIA_URL + filename)
        return Response({"url": url}, status=http_status.HTTP_201_CREATED)

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
        if order.trip_id:
            order.trip.status = "closed"
            order.trip.arrival_at = order.completed_at
            order.trip.save(update_fields=["status", "arrival_at", "updated_at"])
        if order.vehicle_id:
            set_vehicle_status(order.vehicle, "available", trip=None, place=order.dropoff, reason="Order completed")
        if order.driver_id:
            Driver.objects.filter(pk=order.driver_id).update(status="available")
        order.log("completed", "ORDER_COMPLETED", proof.receiver_name if proof else "Delivered", city=order.dropoff.city)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        was_committed = order.status in ("assigned", "dispatched")
        order.status = "cancelled"
        order.save(update_fields=["status", "updated_at"])
        if was_committed and order.vehicle_id and order.vehicle.current_trip_id == order.trip_id:
            # Only release the vehicle if it is still on this order's own trip - a
            # vehicle already reallocated elsewhere should not be yanked back.
            set_vehicle_status(order.vehicle, "available", trip=None, reason="Order cancelled")
        order.log("cancelled", "ORDER_CANCELLED", request.data.get("reason", ""))
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def reprice(self, request, pk=None):
        order = self.get_object()
        breakdown = order.price_from_rate_card()
        if breakdown is None:
            raise ValidationError("Link a rate card to this order before repricing.")
        return Response({"order": self.get_serializer(order).data, "breakdown": breakdown})

    @action(detail=False, methods=["get"])
    def billable(self, request):
        """Delivered consignments with no invoice yet, priced from their rate card.

        The Invoices board offered a create form that took freight, GST and the
        total as typed numbers against a *trip*, with no link to a consignment -
        so an invoice raised there could not reach a rate card at all. This is
        the list that form picks from instead, which is what makes the amount
        come from `price_from_rate_card` rather than from whoever is typing.

        Orders that cannot be billed yet are still returned, carrying the reason,
        so the desk can see what is waiting on an ePOD rather than wondering why
        a delivered consignment is missing.
        """
        orders = (self.get_queryset().filter(status="completed", invoices__isnull=True, consolidated_invoices__isnull=True)
                  .select_related("customer", "service_rate", "pickup", "dropoff")
                  .prefetch_related("proofs").order_by("-completed_at"))
        rows = []
        for order in orders:
            # save=False: this is a preview, and listing a page of orders must not
            # rewrite their stored freight as a side effect.
            breakdown = order.price_from_rate_card(save=False)
            blocked = ""
            if order.pod_required and not order_pod_state(order)[1]:
                blocked = "ePOD not verified yet"
            elif not order.total_amount:
                blocked = "No rate card priced against this consignment"
            rows.append({
                "id": order.pk, "number": order.number, "customer_name": order.customer.name,
                "route": f"{order.pickup.city} → {order.dropoff.city}",
                "completed_at": order.completed_at, "distance_km": float(order.distance_km or 0),
                "weight_kg": float(order.weight_kg or 0),
                "rate_card": order.service_rate.name if order.service_rate else "",
                "freight_amount": float(order.freight_amount or 0),
                "tax_amount": float(order.tax_amount or 0),
                "total_amount": float(order.total_amount or 0),
                "breakdown": breakdown, "blocked_reason": blocked,
            })
        # What can be billed now comes first; what is waiting on an ePOD is
        # still worth seeing, but not at the top of the desk's list.
        rows.sort(key=lambda row: (bool(row["blocked_reason"]), row["number"]))
        return Response({"count": len(rows), "orders": rows,
                         "billable_now": sum(1 for row in rows if not row["blocked_reason"])})

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

    @action(detail=True, methods=["post"], url_path="generate-lr")
    @transaction.atomic
    def generate_lr(self, request, pk=None):
        """Issue the lorry receipt from what is already on the order, instead of
        an operator re-typing consignor/consignee/origin/destination/material by
        hand into a separate form. See docs/ONE-TRIP-END-TO-END.md §3.1."""
        order = self.get_object()
        lr, created = build_lr_from_order(order)
        return Response({"lorry_receipt": LorryReceiptSerializer(lr).data, "created": created,
                         "order": self.get_serializer(order).data},
                        status=http_status.HTTP_201_CREATED if created else http_status.HTTP_200_OK)

    @action(detail=True, methods=["get"])
    def settlement(self, request, pk=None):
        """The four-sided settlement sheet: what the customer owes, what the vendor
        is owed, what the driver is owed, and what the vehicle actually cost - on
        one screen, so nobody has to open four modules to see if a trip is closed out.
        """
        order = self.get_object()
        invoice = order_invoice(order)
        customer_side = None
        if invoice:
            received = invoice.allocations.aggregate(value=Sum("amount"))["value"] or 0
            customer_side = {"invoice_number": invoice.number, "total_amount": float(invoice.total_amount),
                             "received": float(money(received)), "outstanding": float(money(invoice.total_amount - received)),
                             "status": invoice.status}

        hire = order.hires.exclude(status="cancelled").order_by("-created_at").first()
        vendor_side = None
        if hire:
            payable = vendor_payable(hire)
            bill = hire.bills.order_by("-created_at").first()
            vendor_side = {"vendor": hire.vendor.name, "hire_status": hire.status, "payable": payable,
                          "bill_number": bill.number if bill else None,
                          "bill_status": bill.status if bill else None}

        driver_side = None
        if order.trip_id:
            settlement = Settlement.objects.filter(trip_id=order.trip_id).order_by("-created_at").first()
            if settlement:
                driver_side = {"driver": settlement.driver.name, "advance_amount": float(settlement.advance_amount),
                               "approved_expenses": float(settlement.approved_expenses),
                               "net_payable": float(settlement.net_payable), "status": settlement.status}

        # Apportioned, not the whole trip's cost charged again to this one order -
        # see docs/ONE-TRIP-END-TO-END.md §3.2/§5 Phase 2 for the same bug this
        # fixed in order_profitability.
        if order.trip_id:
            row = apportion_trip_cost(order.trip)["orders"].get(
                order.id, {"fuel": Decimal("0"), "shared_expenses": Decimal("0"), "attributed_expenses": Decimal("0")})
            fuel = row["fuel"]
            expenses = row["shared_expenses"] + row["attributed_expenses"]
        else:
            fuel = Decimal("0")
            expenses = money(TripExpense.objects.filter(order=order).aggregate(value=Sum("amount"))["value"] or 0)
        vehicle_side = {"fuel": float(fuel), "trip_expenses": float(expenses), "total_cost": float(money(fuel + expenses))}

        revenue = money(invoice.total_amount if invoice else order.total_amount)
        vendor_cost = money(vendor_side["payable"]["gross_amount"]) if vendor_side else Decimal("0")
        total_cost = money(vendor_cost + fuel + expenses)
        actual_profit = money(revenue - total_cost)

        return Response({
            "order": order.number, "order_status": order.status,
            "customer": customer_side, "vendor": vendor_side, "driver": driver_side, "vehicle": vehicle_side,
            "revenue": float(revenue), "total_cost": float(total_cost), "actual_profit": float(actual_profit),
            "settled": bool(customer_side and customer_side["outstanding"] <= 0
                          and (not vendor_side or (vendor_side["payable"]["balance_due"] <= 0))),
        })

    @action(detail=True, methods=["post"], url_path="recommend-vehicles")
    def recommend_vehicles_action(self, request, pk=None):
        """The ranked comparison table: own and vendor capacity, dead km, cost,
        revenue and expected profit, best option first - not simply the nearest.
        """
        order = self.get_object()
        max_dead_km = request.data.get("max_dead_km")
        candidates = recommend_vehicles(order, max_dead_km=Decimal(str(max_dead_km)) if max_dead_km else None,
                                        include_vendor=request.data.get("include_vendor", True))
        return Response({"order": order.number, "count": len(candidates), "candidates": candidates})

    @action(detail=True, methods=["post"], url_path="confirm-vehicle")
    @transaction.atomic
    def confirm_vehicle(self, request, pk=None):
        """Commit a vehicle to the order: link it (creating a Vehicle row for a
        vendor's own truck when one is not already on file), create the trip, raise
        a VehicleHire when it is outside-sourced, check document validity, and send
        the vendor confirmation - the whole of allocation in one call.

        A driver is optional here: an outside-sourced trip's driver is the vendor's
        own person, recorded on the hire, not forced into the internal driver master.
        Without a driver, `ensure_trip` cannot yet open a trip - one is created the
        moment a driver is linked (through `assign`, or a follow-up call here).
        """
        order = self.get_object()
        vehicle_id = request.data.get("vehicle")
        vendor_id = request.data.get("vendor")
        driver_id = request.data.get("driver")
        outside = request.data.get("outside_vehicle") or {}
        hire_terms = request.data.get("hire") or {}

        vehicle = None
        if vehicle_id:
            vehicle = Vehicle.objects.get(pk=vehicle_id)
        elif outside.get("vehicle_number") and vendor_id:
            vendor = Vendor.objects.get(pk=vendor_id)
            vehicle, _ = Vehicle.objects.get_or_create(
                registration_number=outside["vehicle_number"],
                defaults={"vehicle_type": outside.get("vehicle_type", ""), "capacity_kg": outside.get("capacity_kg") or 0,
                         "ownership": "outside", "vendor": vendor, "status": "allocated"})
        if not vehicle:
            raise ValidationError("Provide a vehicle, or outside_vehicle details with a vendor, to confirm an allocation.")

        order.vehicle = vehicle
        if driver_id:
            order.driver = Driver.objects.get(pk=driver_id)
        if vendor_id:
            order.vendor = Vendor.objects.get(pk=vendor_id)
        elif vehicle.vendor_id:
            order.vendor = vehicle.vendor
        order.status = "assigned"
        order.save()
        trip = order.ensure_trip()
        set_vehicle_status(vehicle, "allocated", trip=trip, reason=f"Confirmed for order {order.number}")

        today = timezone.localdate()
        expired = ComplianceDocument.objects.filter(vehicle=vehicle, expiry_date__isnull=False, expiry_date__lt=today)
        warnings = [f"{doc.get_document_type_display()} on {vehicle.registration_number} expired {doc.expiry_date}."
                   for doc in expired]

        hire, confirmation_sent = None, False
        if vehicle.ownership in ("outside", "attached") and order.vendor_id:
            hire = VehicleHire.objects.create(
                order=order, trip=trip, vendor_id=order.vendor_id, hire_type=hire_terms.get("hire_type", "spot"),
                outside_vehicle_number=vehicle.registration_number if vehicle.ownership == "outside" else "",
                outside_vehicle_type=vehicle.vehicle_type, outside_capacity_kg=vehicle.capacity_kg,
                driver_name=hire_terms.get("driver_name", ""), driver_phone=hire_terms.get("driver_phone", ""),
                driver_licence=hire_terms.get("driver_licence", ""), gps_available=bool(hire_terms.get("gps_available")),
                agreed_rate=hire_terms.get("agreed_rate") or 0, rate_basis=hire_terms.get("rate_basis", "trip"),
                loading_charge=hire_terms.get("loading_charge") or 0, unloading_charge=hire_terms.get("unloading_charge") or 0,
                detention_rate_per_day=hire_terms.get("detention_rate_per_day") or 0,
                toll_responsibility=hire_terms.get("toll_responsibility", "vendor"),
                other_charges=hire_terms.get("other_charges") or 0, advance_amount=hire_terms.get("advance_amount") or 0,
                payment_terms_days=hire_terms.get("payment_terms_days") or order.vendor.payment_terms_days,
                status="confirmed")
            if hire.vendor.email:
                subject, body = confirmation_email(hire)
                outbound_messaging.send_email(to=hire.vendor.email, subject=subject, body=body,
                                              template_key="vendor_confirmation", reference_type="hire",
                                              reference_id=hire.pk, created_by=request.user.get_username())
                confirmation_sent = True
            else:
                warnings.append(f"{hire.vendor.name} has no email on file - the confirmation was not sent.")

        order.log("assigned", "ORDER_VEHICLE_CONFIRMED",
                  f"{vehicle.registration_number} confirmed" + (f" via {order.vendor.name}" if order.vendor_id else ""))
        return Response({"order": self.get_serializer(order).data,
                         "hire": VehicleHireSerializer(hire).data if hire else None,
                         "warnings": warnings, "vendor_confirmation_sent": confirmation_sent})


class VehicleHireViewSet(FilterableViewSet):
    """The commercial terms of an outside-sourced trip, and its settlement."""
    queryset = VehicleHire.objects.select_related("order", "vendor", "trip").all()
    serializer_class = VehicleHireSerializer
    required_permission = "operations.view"; required_write_permission = "operations.manage"
    filter_fields = ["order", "vendor", "trip", "status", "hire_type"]
    search_fields = ["outside_vehicle_number", "driver_name", "vendor__name"]

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        hire = self.get_object()
        if hire.status != "draft":
            raise ValidationError(f"A hire that is {hire.status} cannot be confirmed again.")
        hire.status = "confirmed"
        hire.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(hire).data)

    @action(detail=True, methods=["get"])
    def payable(self, request, pk=None):
        hire = self.get_object()
        detention_days = request.query_params.get("detention_days")
        return Response(vendor_payable(hire, detention_days=Decimal(detention_days) if detention_days else None))

    @action(detail=True, methods=["post"], url_path="raise-bill")
    @transaction.atomic
    def raise_bill(self, request, pk=None):
        hire = self.get_object()
        try:
            bill, created = raise_vendor_bill(hire, detention_days=request.data.get("detention_days"),
                                             created_by=request.user.get_username())
        except HireBillingError as error:
            raise ValidationError(str(error))
        ledger, ledger_error = None, ""
        if request.data.get("post_to_ledger", True):
            from accounting.services import post_vendor_bill
            try:
                entry = post_vendor_bill(bill, created_by=request.user.get_username())
                ledger = {"number": entry.number, "id": entry.pk}
            except PostingError as error:
                ledger_error = str(error)
        return Response({"bill_number": bill.number, "bill_id": bill.pk, "created": created,
                         "journal_entry": ledger, "ledger_error": ledger_error,
                         "hire": self.get_serializer(hire).data},
                        status=http_status.HTTP_201_CREATED if created else http_status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="send-confirmation")
    def send_confirmation(self, request, pk=None):
        """Email the transport owner what they need to put a truck on the road,
        recorded and resendable through the outbound-messages log."""
        hire = self.get_object()
        if not hire.vendor.email:
            raise ValidationError(f"{hire.vendor.name} has no email address on file.")
        subject, body = confirmation_email(hire)
        message = outbound_messaging.send_email(to=hire.vendor.email, subject=subject, body=body,
                                                template_key="vendor_confirmation", reference_type="hire",
                                                reference_id=hire.pk, created_by=request.user.get_username())
        return Response({"message_id": message.pk, "status": message.status, "to": message.to_address,
                         "error": message.error})


class VendorLaneRateViewSet(FilterableViewSet):
    """A vendor's negotiated contract rate for one lane - read by the dispatch
    planner's lane-level spot pricing and by RFQ fan-out (docs/DISPATCH-PLANNER-V2.md §7.2)."""
    queryset = VendorLaneRate.objects.select_related("vendor").all()
    serializer_class = VendorLaneRateSerializer
    required_permission = "operations.view"; required_write_permission = "operations.manage"
    filter_fields = ["vendor", "origin_city", "destination_city", "temperature_class", "active"]
    search_fields = ["origin_city", "destination_city", "vendor__name"]


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
    filter_fields = ["order", "proof_type", "status", "damage_reported", "physical_copy_required", "courier_status"]
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

    # --- physical copy by courier -------------------------------------------
    # Some consignees only sign a physical LR/POD. The driver collects it and sends it back by
    # courier - tracked here as state this office updates by hand, with no live courier API.

    @action(detail=False, methods=["get"], url_path="couriers-pending")
    def couriers_pending(self, request):
        """Physical copies still out with a courier, oldest dispatch first."""
        records = self.get_queryset().filter(physical_copy_required=True).exclude(courier_status="delivered").order_by("courier_dispatched_at")
        return Response({"count": records.count(), "proofs": self.get_serializer(records, many=True).data})

    @action(detail=True, methods=["post"], url_path="courier-dispatch")
    def courier_dispatch(self, request, pk=None):
        proof = self.get_object()
        name = (request.data.get("courier_name") or "").strip()
        awb = (request.data.get("awb_number") or "").strip()
        if not name or not awb:
            raise ValidationError("Courier name and AWB number are required to dispatch the physical POD.")
        expected_by = request.data.get("expected_by") or None
        if expected_by:
            try:
                expected_by = date.fromisoformat(str(expected_by))
            except ValueError:
                raise ValidationError("expected_by must be a date in YYYY-MM-DD format.")
        proof.dispatch_by_courier(name, awb, expected_by=expected_by, remarks=request.data.get("remarks", ""))
        proof.order.log(proof.order.status, "POD_COURIER_DISPATCHED", f"Physical POD sent via {name}, AWB {awb}")
        return Response(self.get_serializer(proof).data)

    @action(detail=True, methods=["post"], url_path="courier-transit")
    def courier_transit(self, request, pk=None):
        proof = self.get_object()
        if proof.courier_status not in ("dispatched", "in_transit"):
            raise ValidationError("This physical copy has not been dispatched by courier yet.")
        proof.mark_courier_in_transit()
        return Response(self.get_serializer(proof).data)

    @action(detail=True, methods=["post"], url_path="courier-received")
    def courier_received(self, request, pk=None):
        proof = self.get_object()
        if proof.courier_status == "not_sent":
            raise ValidationError("This physical copy has not been dispatched by courier yet.")
        proof.receive_from_courier()
        proof.order.log(proof.order.status, "POD_COURIER_RECEIVED", f"Physical POD received back from {proof.courier_name}")
        return Response(self.get_serializer(proof).data)

    @action(detail=True, methods=["post"], url_path="courier-lost")
    def courier_lost(self, request, pk=None):
        proof = self.get_object()
        if proof.courier_status == "not_sent":
            raise ValidationError("This physical copy has not been dispatched by courier yet.")
        proof.mark_courier_lost(request.data.get("remarks", ""))
        proof.order.log(proof.order.status, "POD_COURIER_LOST", f"Physical POD lost in transit with {proof.courier_name}")
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


@requires("reports.view")
@api_view(["GET"])
def live_tracking(request):
    """Proxy the Geotrackers dashboard feed, merge it with FMS vehicles and
    write the positions back so the dispatch solver and geofence capture see
    fresh coordinates.

    Every device the vendor reports is returned, matched to an FMS vehicle or
    not, so a truck whose registration has not been keyed in yet still shows on
    the map instead of vanishing silently.

    `?debug=1` returns the raw vendor payload and what the parser made of it -
    the endpoint is undocumented, so when a field moves this is how you find
    out where it moved to.
    """
    raw, error = geotrackers.fetch_dashboard()

    if request.query_params.get("debug"):
        rows = geotrackers.extract_rows(raw or {})
        return Response({
            "error": error,
            "payload_type": type(raw).__name__,
            "top_level_keys": sorted(raw.keys())[:40] if isinstance(raw, dict) else None,
            "rows_detected": len(rows),
            "first_row_raw": rows[0] if rows else None,
            "first_row_parsed": geotrackers.row_to_ping(rows[0]) if rows else None,
            "raw_excerpt": _json.dumps(raw)[:4000] if raw is not None else None,
        })

    pings = geotrackers.parse(raw or {})

    # One pass over the fleet, indexed on the registration with its punctuation
    # stripped - the vendor and the FMS rarely agree on spacing.
    fleet = {geotrackers.normalise_registration(v.registration_number): v
             for v in Vehicle.objects.only("id", "registration_number", "status",
                                           "current_latitude", "current_longitude")}

    stale = []
    for ping in pings:
        vehicle = fleet.get(geotrackers.normalise_registration(ping["reg"]))
        ping["fms_id"] = vehicle.id if vehicle else None
        ping["fms_status"] = vehicle.status if vehicle else None
        if vehicle and ping["in_india"]:
            vehicle.current_latitude = Decimal(str(round(ping["lat"], 6)))
            vehicle.current_longitude = Decimal(str(round(ping["lng"], 6)))
            stale.append(vehicle)

    if stale:
        Vehicle.objects.bulk_update(stale, ["current_latitude", "current_longitude"])

    located = [p for p in pings if p["in_india"]]
    payload = {
        "vehicles": pings,
        "total": len(pings),
        "located": len(located),
        "matched": sum(1 for p in pings if p["fms_id"]),
        "source": "geotrackers",
    }
    if error:
        payload["error"] = error
    elif pings and not located:
        # The feed answered, so this is a parsing problem rather than an outage.
        payload["error"] = ("Geotrackers returned %d vehicles but no usable coordinates. "
                            "Open /api/v1/live-tracking/?debug=1 to see the raw fields." % len(pings))
    return Response(payload)


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
        order.ensure_trip()
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
    """Revenue, diesel and on-road cost for one consignment.

    When the order rides on a trip with other orders, its diesel and shared
    on-road cost are its *apportioned share* of the trip's total
    (`fleet.costing.apportion_trip_cost`), not the trip's full cost charged
    again to every order on it - see docs/ONE-TRIP-END-TO-END.md §3.2/§5
    Phase 2 for why that used to double- (or quintuple-) count fuel and drop
    trip-keyed expenses from every order's P&L entirely.
    """
    order = Order.objects.filter(pk=pk).select_related("vehicle", "trip").first()
    if not order:
        return Response({"detail": "Order not found."}, status=404)
    revenue = money(order.total_amount)
    if order.trip_id:
        split = apportion_trip_cost(order.trip)
        row = split["orders"].get(order.id, {"fuel": Decimal("0"), "shared_expenses": Decimal("0"),
                                              "attributed_expenses": Decimal("0"), "total_cost": Decimal("0")})
        fuel = row["fuel"]
        expenses = row["shared_expenses"] + row["attributed_expenses"]
        cost = row["total_cost"]
        cost_basis = split["basis"]
    else:
        # No trip yet - the only cost that can exist is whatever was booked
        # directly against this order, with nothing to share or apportion.
        expenses = money(TripExpense.objects.filter(order=order).aggregate(value=Sum("amount"))["value"] or 0)
        fuel = Decimal("0")
        cost = expenses
        cost_basis = "order_only"
    profit = money(revenue - cost)
    return Response({
        "order": order.number, "vehicle": getattr(order.vehicle, "registration_number", ""),
        "revenue": float(revenue), "trip_expenses": float(expenses), "fuel": float(fuel),
        "total_cost": float(cost), "profit": float(profit),
        "margin_percent": float(round(profit / revenue * 100, 2)) if revenue else 0.0,
        "cost_per_km": float(money(cost / order.distance_km)) if order.distance_km else 0.0,
        "cost_basis": cost_basis,
    })


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@requires("reports.view")
@api_view(["GET"])
def report_driver_availability(request):
    """Driver roster with current availability and licence expiry status."""
    from datetime import timedelta
    today = timezone.localdate()
    qs = Driver.objects.all().order_by("status", "name")
    rows = []
    for d in qs:
        expiry = d.licence_expiry
        if expiry is None:
            expiry_status = "unknown"
        elif expiry < today:
            expiry_status = "expired"
        elif expiry <= today + timedelta(days=30):
            expiry_status = "expiring_soon"
        else:
            expiry_status = "valid"
        rows.append({
            "id": d.id,
            "name": d.name,
            "phone": d.phone,
            "licence_number": d.licence_number,
            "licence_expiry": str(expiry) if expiry else None,
            "licence_expiry_status": expiry_status,
            "status": d.status,
            "home_city": d.home_city,
            "date_of_joining": str(d.date_of_joining) if d.date_of_joining else None,
            "monthly_salary": float(d.monthly_salary),
            "active_trips": Trip.objects.filter(driver=d).exclude(status__in=["closed", "cancelled"]).count(),
        })
    summary = {
        "total": len(rows),
        "available": sum(1 for r in rows if r["status"] == "available"),
        "on_trip": sum(1 for r in rows if r["status"] in ("on_trip", "running")),
        "licences_expired": sum(1 for r in rows if r["licence_expiry_status"] == "expired"),
        "licences_expiring_soon": sum(1 for r in rows if r["licence_expiry_status"] == "expiring_soon"),
    }
    return Response({"summary": summary, "drivers": rows})


@requires("reports.view")
@api_view(["GET"])
def report_fleet(request):
    """Fleet-wide vehicle status, utilisation and compliance snapshot."""
    from datetime import timedelta
    today = timezone.localdate()
    qs = Vehicle.objects.select_related("vendor", "current_place").all().order_by("status", "registration_number")
    rows = []
    for v in qs:
        ins_exp = v.insurance_expiry
        per_exp = v.permit_expiry
        def exp_status(date):
            if date is None:
                return "unknown"
            if date < today:
                return "expired"
            if date <= today + timedelta(days=30):
                return "expiring_soon"
            return "valid"
        rows.append({
            "id": v.id,
            "registration_number": v.registration_number,
            "vehicle_type": v.vehicle_type,
            "ownership": v.ownership,
            "capacity_kg": v.capacity_kg,
            "status": v.status,
            "fuel_type": v.fuel_type,
            "insurance_expiry": str(ins_exp) if ins_exp else None,
            "insurance_status": exp_status(ins_exp),
            "permit_expiry": str(per_exp) if per_exp else None,
            "permit_status": exp_status(per_exp),
            "current_odometer_km": v.current_odometer_km,
            "vendor_name": v.vendor.name if v.vendor else "",
            "current_place": v.current_place.name if v.current_place else "",
            "trips_total": Trip.objects.filter(vehicle=v).count(),
        })
    summary = {
        "total": len(rows),
        "available": sum(1 for r in rows if r["status"] == "available"),
        "on_trip": sum(1 for r in rows if r["status"] in ("on_trip", "running")),
        "under_maintenance": sum(1 for r in rows if r["status"] == "under_maintenance"),
        "inactive": sum(1 for r in rows if r["status"] == "inactive"),
        "insurance_expired": sum(1 for r in rows if r["insurance_status"] == "expired"),
        "permit_expired": sum(1 for r in rows if r["permit_status"] == "expired"),
    }
    return Response({"summary": summary, "vehicles": rows})


@requires("reports.view")
@api_view(["GET"])
def report_customer_invoices(request):
    """Customer-wise invoice ageing and outstanding summary."""
    from_date = request.query_params.get("from")
    to_date = request.query_params.get("to")
    today = timezone.localdate()
    qs = Invoice.objects.select_related("customer").order_by("-created_at")
    if from_date:
        qs = qs.filter(created_at__date__gte=from_date)
    if to_date:
        qs = qs.filter(created_at__date__lte=to_date)
    rows = []
    for inv in qs:
        days_overdue = (today - inv.due_date).days if inv.status != "paid" else 0
        rows.append({
            "id": inv.id,
            "number": inv.number,
            "customer": inv.customer.name,
            "customer_gstin": inv.customer.gstin,
            "freight_amount": float(inv.freight_amount),
            "additional_charges": float(inv.additional_charges),
            "tax_amount": float(inv.tax_amount),
            "total_amount": float(inv.total_amount),
            "due_date": str(inv.due_date),
            "status": inv.status,
            "days_overdue": max(0, days_overdue) if inv.status != "paid" else 0,
            "gst_percent": float(inv.gst_percent),
            "reverse_charge": inv.reverse_charge,
            "created_at": inv.created_at.strftime("%Y-%m-%d"),
        })
    # Customer-level aggregation
    from collections import defaultdict
    by_customer: dict = defaultdict(lambda: {"total": 0.0, "paid": 0.0, "outstanding": 0.0, "count": 0})
    for r in rows:
        c = by_customer[r["customer"]]
        c["count"] += 1
        c["total"] += r["total_amount"]
        if r["status"] == "paid":
            c["paid"] += r["total_amount"]
        else:
            c["outstanding"] += r["total_amount"]
    summary = {
        "total_invoiced": sum(r["total_amount"] for r in rows),
        "total_paid": sum(r["total_amount"] for r in rows if r["status"] == "paid"),
        "total_outstanding": sum(r["total_amount"] for r in rows if r["status"] != "paid"),
        "invoice_count": len(rows),
        "overdue_count": sum(1 for r in rows if r["days_overdue"] > 0),
    }
    customer_summary = [{"customer": k, **v} for k, v in by_customer.items()]
    return Response({"summary": summary, "invoices": rows, "by_customer": customer_summary})


@requires("reports.view")
@api_view(["GET"])
def report_vehicle_settlement(request):
    """Trip-level settlement register: advance, expenses, and net payable per driver."""
    from_date = request.query_params.get("from")
    to_date = request.query_params.get("to")
    qs = Settlement.objects.select_related("trip__vehicle", "driver").order_by("-created_at")
    if from_date:
        qs = qs.filter(created_at__date__gte=from_date)
    if to_date:
        qs = qs.filter(created_at__date__lte=to_date)
    rows = []
    for s in qs:
        rows.append({
            "id": s.id,
            "driver": s.driver.name,
            "driver_phone": s.driver.phone,
            "trip": s.trip.number,
            "vehicle": s.trip.vehicle.registration_number,
            "route": f"{s.trip.origin} → {s.trip.destination}",
            "advance_amount": float(s.advance_amount),
            "approved_expenses": float(s.approved_expenses),
            "net_payable": float(s.net_payable),
            "status": s.status,
            "trip_status": s.trip.status,
            "created_at": s.created_at.strftime("%Y-%m-%d"),
        })
    summary = {
        "total_advance": sum(r["advance_amount"] for r in rows),
        "total_approved_expenses": sum(r["approved_expenses"] for r in rows),
        "total_net_payable": sum(r["net_payable"] for r in rows),
        "pending_count": sum(1 for r in rows if r["status"] == "pending"),
        "settled_count": sum(1 for r in rows if r["status"] == "settled"),
        "settlement_count": len(rows),
    }
    return Response({"summary": summary, "settlements": rows})


@requires("reports.view")
@api_view(["GET"])
def report_trip_profitability(request):
    """Trip-wise P&L across a date range: revenue, fuel, on-road cost, advance
    and margin per trip - using the same apportioned cost as the Trip Cockpit
    (`fleet.costing.apportion_trip_cost`), so this report and that screen can
    never disagree with each other. See docs/ONE-TRIP-END-TO-END.md §5 Phase 5.
    """
    from_date = request.query_params.get("from")
    to_date = request.query_params.get("to")
    vehicle_id = request.query_params.get("vehicle")
    driver_id = request.query_params.get("driver")
    branch_id = request.query_params.get("branch")
    customer_id = request.query_params.get("customer")
    origin_city = request.query_params.get("origin_city")
    destination_city = request.query_params.get("destination_city")

    qs = Trip.objects.select_related("vehicle", "driver").prefetch_related("orders__customer").order_by("-created_at")
    if from_date:
        qs = qs.filter(created_at__date__gte=from_date)
    if to_date:
        qs = qs.filter(created_at__date__lte=to_date)
    if vehicle_id:
        qs = qs.filter(vehicle_id=vehicle_id)
    if driver_id:
        qs = qs.filter(driver_id=driver_id)
    if branch_id:
        qs = qs.filter(orders__branch_id=branch_id)
    if customer_id:
        qs = qs.filter(orders__customer_id=customer_id)
    if origin_city:
        qs = qs.filter(origin__icontains=origin_city)
    if destination_city:
        qs = qs.filter(destination__icontains=destination_city)
    if branch_id or customer_id:
        qs = qs.distinct()

    rows = []
    for trip in qs:
        orders = list(trip.orders.all())
        if not orders:
            continue   # a trip with nothing on it yet has no revenue to report
        split = apportion_trip_cost(trip)
        revenue = money(sum((money(o.total_amount) for o in orders), Decimal("0")))
        cost = money(sum((row["total_cost"] for row in split["orders"].values()), Decimal("0")))
        margin = money(revenue - cost)
        distance = trip.passed_km or trip.running_km or 0
        rows.append({
            "id": trip.id, "number": trip.number, "status": trip.status,
            "vehicle": trip.vehicle.registration_number, "driver": trip.driver.name,
            "route": f"{trip.origin} → {trip.destination}", "order_count": len(orders),
            "customers": sorted({o.customer.name for o in orders}),
            "revenue": float(revenue), "fuel": float(split["shared_fuel"]),
            "on_road": float(split["shared_expenses"]), "advance": float(money(trip.advance_amount)),
            "cost": float(cost), "margin": float(margin),
            "margin_percent": float(round(margin / revenue * 100, 2)) if revenue else 0.0,
            "distance_km": distance,
            "cost_per_km": float(money(cost / distance)) if distance else 0.0,
            "revenue_per_km": float(money(revenue / distance)) if distance else 0.0,
            "cost_basis": split["basis"], "created_at": trip.created_at.strftime("%Y-%m-%d"),
        })

    summary = {
        "trip_count": len(rows), "total_revenue": sum(r["revenue"] for r in rows),
        "total_cost": sum(r["cost"] for r in rows), "total_margin": sum(r["margin"] for r in rows),
        "avg_margin_percent": float(round(sum(r["margin_percent"] for r in rows) / len(rows), 2)) if rows else 0.0,
    }
    return Response({"summary": summary, "trips": rows})


@requires("reports.view")
@api_view(["GET"])
def report_sales(request):
    """Sales pipeline: quotes, conversions, and revenue by customer and period."""
    from_date = request.query_params.get("from")
    to_date = request.query_params.get("to")
    qs = SalesQuote.objects.select_related("customer").order_by("-created_at")
    if from_date:
        qs = qs.filter(created_at__date__gte=from_date)
    if to_date:
        qs = qs.filter(created_at__date__lte=to_date)
    rows = []
    for q in qs:
        rows.append({
            "id": q.id,
            "number": q.number,
            "customer": q.customer.name,
            "customer_gstin": q.customer.gstin,
            "origin": q.origin,
            "destination": q.destination,
            "lane": f"{q.origin} → {q.destination}",
            "freight_amount": float(q.freight_amount),
            "valid_until": str(q.valid_until),
            "status": q.status,
            "created_at": q.created_at.strftime("%Y-%m-%d"),
        })
    by_status: dict = {}
    for r in rows:
        by_status.setdefault(r["status"], {"count": 0, "value": 0.0})
        by_status[r["status"]]["count"] += 1
        by_status[r["status"]]["value"] += r["freight_amount"]
    total_value = sum(r["freight_amount"] for r in rows)
    won_value = by_status.get("accepted", {}).get("value", 0.0) + by_status.get("won", {}).get("value", 0.0)
    summary = {
        "total_quotes": len(rows),
        "total_value": total_value,
        "won_value": won_value,
        "conversion_rate": round(won_value / total_value * 100, 1) if total_value else 0.0,
        "by_status": [{"status": k, **v} for k, v in by_status.items()],
    }
    return Response({"summary": summary, "quotes": rows})
