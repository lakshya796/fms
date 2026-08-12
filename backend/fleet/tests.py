"""Tests for the Fleetbase FleetOps inspired modules."""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounting.models import JournalEntry
from iam.models import OutboundMessage
from . import geotrackers
from .models import (ComplianceDocument, Customer, Driver, Fleet, FuelEntry, Invoice, Issue, MaintenanceSchedule, Order,
                     Place, ProofOfDelivery, ServiceArea, ServiceRate, Trip, TripExpense, Vehicle, VehicleHire, Vendor,
                     Waypoint, Zone, haversine_km)


class BaseFleetOpsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("fleetadmin", password="test-only-password")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.customer = Customer.objects.create(name="Tata Consumer Products", gstin="27AAACT2727Q1ZW", kyc_status="verified")
        self.area = ServiceArea.objects.create(name="West India", code="WEST", states="Maharashtra, Gujarat")
        self.pickup = Place.objects.create(name="Bhiwandi warehouse", code="PL-BHW", city="Bhiwandi", state="Maharashtra",
                                           service_area=self.area, latitude=Decimal("19.296700"), longitude=Decimal("73.063100"))
        self.dropoff = Place.objects.create(name="Chakan DC", code="PL-CKN", city="Chakan", state="Maharashtra",
                                            service_area=self.area, latitude=Decimal("18.760600"), longitude=Decimal("73.863600"))
        self.vehicle = Vehicle.objects.create(registration_number="MH 04 JU 9182", vehicle_type="32 ft MXL",
                                              capacity_kg=16000, current_odometer_km=268400)
        self.driver = Driver.objects.create(name="Ramesh Yadav", phone="+919820011223", licence_number="MH0320180001234")
        self.rate = ServiceRate.objects.create(name="Mumbai-Pune 32ft", code="RC-MUMPUN", service_area=self.area,
                                               rate_type="per_km", base_charge=2500, per_km_rate=48, minimum_charge=8000,
                                               loading_charge=1800, unloading_charge=1500, halting_charge_per_day=2500,
                                               fuel_surcharge_percent=Decimal("3.50"), gst_percent=5)


class RateCardTests(BaseFleetOpsTest):
    def test_per_km_quote_includes_surcharge_handling_and_gst(self):
        breakdown = self.rate.quote(distance_km=150, weight_kg=12400)
        self.assertEqual(breakdown["freight"], 9700.0)            # 2500 base + 150 km x 48
        self.assertEqual(breakdown["fuel_surcharge"], 339.5)      # 3.5% of freight
        self.assertEqual(breakdown["handling_charges"], 3300.0)   # loading + unloading
        self.assertEqual(breakdown["taxable_value"], 13339.5)
        self.assertEqual(breakdown["gst_amount"], 666.98)
        self.assertEqual(breakdown["total"], 14006.48)

    def test_minimum_charge_applies_to_short_lanes(self):
        breakdown = self.rate.quote(distance_km=10)
        self.assertEqual(breakdown["freight"], 8000.0)

    def test_reverse_charge_moves_gst_to_the_consignee(self):
        self.rate.reverse_charge = True
        self.rate.save(update_fields=["reverse_charge"])
        breakdown = self.rate.quote(distance_km=150)
        self.assertEqual(breakdown["gst_amount"], 0.0)
        self.assertTrue(breakdown["reverse_charge"])
        self.assertEqual(breakdown["total"], breakdown["taxable_value"])

    def test_per_ton_km_rating(self):
        rate = ServiceRate.objects.create(name="Ton km", code="RC-TON", rate_type="per_ton_km",
                                          per_ton_km_rate=Decimal("6.50"), gst_percent=5)
        breakdown = rate.quote(distance_km=500, weight_kg=10000)
        self.assertEqual(breakdown["freight"], 32500.0)           # 10 tonnes x 500 km x 6.50

    def test_quote_endpoint_can_persist_the_estimate(self):
        response = self.client.post("/api/v1/service-rates/quote/", {
            "service_rate": self.rate.id, "origin": "Bhiwandi", "destination": "Chakan",
            "distance_km": 150, "weight_kg": 12400, "customer": self.customer.id, "save_quote": True}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["breakdown"]["total"], 14006.48)
        self.assertEqual(float(response.data["quote"]["total_amount"]), 14006.48)


class ZoneTests(BaseFleetOpsTest):
    def setUp(self):
        super().setUp()
        self.zone = Zone.objects.create(service_area=self.area, name="Mumbai metropolitan", center_latitude=Decimal("19.076000"),
                                        center_longitude=Decimal("72.877700"), radius_km=45)

    def test_haversine_distance_matches_known_lane(self):
        self.assertAlmostEqual(haversine_km(19.076, 72.8777, 18.5204, 73.8567), 118.8, delta=1.5)

    def test_geofence_membership(self):
        self.assertTrue(self.zone.contains(19.2967, 73.0631))     # Bhiwandi is inside the 45 km radius
        self.assertFalse(self.zone.contains(18.5204, 73.8567))    # Pune is outside

    def test_locate_endpoint_returns_matching_zones(self):
        response = self.client.get("/api/v1/zones/locate/", {"lat": 19.2967, "lng": 73.0631})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["zones"][0]["name"], "Mumbai metropolitan")

    def test_locate_endpoint_rejects_missing_coordinates(self):
        self.assertEqual(self.client.get("/api/v1/zones/locate/").status_code, 400)


class OrderLifecycleTests(BaseFleetOpsTest):
    def create_order(self, **overrides):
        payload = {"customer": self.customer.id, "pickup": self.pickup.id, "dropoff": self.dropoff.id,
                   "service_rate": self.rate.id, "order_type": "ftl", "weight_kg": 12400,
                   "payload_description": "Packaged food cartons", "packages": 480}
        payload.update(overrides)
        response = self.client.post("/api/v1/orders/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return Order.objects.get(pk=response.data["id"])

    def test_order_creation_derives_number_tracking_distance_and_price(self):
        order = self.create_order()
        self.assertTrue(order.number.startswith("ORD-"))
        self.assertTrue(order.tracking_number.startswith("PHZ"))
        self.assertGreater(order.distance_km, 0)
        self.assertGreater(order.total_amount, 0)
        self.assertEqual(order.activities.first().code, "ORDER_CREATED")

    def test_dispatch_requires_an_allocation(self):
        order = self.create_order()
        self.assertEqual(self.client.post(f"/api/v1/orders/{order.id}/dispatch/").status_code, 400)

    def test_assign_dispatch_and_complete_flow(self):
        order = self.create_order()
        assigned = self.client.post(f"/api/v1/orders/{order.id}/assign/",
                                    {"driver": self.driver.id, "vehicle": self.vehicle.id}, format="json")
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.data["status"], "assigned")

        dispatched = self.client.post(f"/api/v1/orders/{order.id}/dispatch/")
        self.assertEqual(dispatched.status_code, 200)
        self.vehicle.refresh_from_db(); self.driver.refresh_from_db()
        self.assertEqual(self.vehicle.status, "running")
        self.assertEqual(self.driver.status, "on_trip")

        issued = self.client.post(f"/api/v1/orders/{order.id}/pod-request/", {"receiver_phone": "9820011223"}, format="json")
        self.assertEqual(issued.status_code, 200)
        completed = self.client.post(f"/api/v1/orders/{order.id}/complete/",
                                     {"receiver_name": "Store manager", "otp": issued.data["otp"]}, format="json")
        self.assertEqual(completed.status_code, 200)
        order.refresh_from_db(); self.vehicle.refresh_from_db(); self.driver.refresh_from_db()
        self.assertEqual(order.status, "completed")
        self.assertIsNotNone(order.completed_at)
        self.assertEqual(self.vehicle.status, "available")
        self.assertEqual(self.driver.status, "available")
        proof = ProofOfDelivery.objects.get(order=order)
        self.assertTrue(proof.otp_verified)
        self.assertEqual(proof.status, "verified")
        self.assertEqual([a.code for a in order.activities.all()][0], "ORDER_COMPLETED")
        self.assertIsNotNone(order.trip_id)
        self.assertEqual(order.trip.status, "closed")

    def test_assigning_an_order_creates_and_reuses_one_trip(self):
        order = self.create_order()
        self.client.post(f"/api/v1/orders/{order.id}/assign/",
                         {"driver": self.driver.id, "vehicle": self.vehicle.id}, format="json")
        order.refresh_from_db()
        self.assertIsNotNone(order.trip_id)
        first_trip_id = order.trip_id
        # Re-assigning the same order must not spawn a second trip and lose the cost history.
        self.client.post(f"/api/v1/orders/{order.id}/assign/",
                         {"driver": self.driver.id, "vehicle": self.vehicle.id}, format="json")
        order.refresh_from_db()
        self.assertEqual(order.trip_id, first_trip_id)

    def test_order_profitability_counts_fuel_logged_against_the_orders_trip(self):
        order = self.create_order()
        self.client.post(f"/api/v1/orders/{order.id}/assign/",
                         {"driver": self.driver.id, "vehicle": self.vehicle.id}, format="json")
        order.refresh_from_db()
        FuelEntry.objects.create(vehicle=self.vehicle, trip=order.trip, odometer_km=self.vehicle.current_odometer_km + 200,
                                 volume_litres=Decimal("50"), rate_per_litre=Decimal("95"))
        response = self.client.get(f"/api/v1/orders/{order.id}/profitability/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["fuel"], 4750.0)

    def test_activity_endpoint_validates_status(self):
        order = self.create_order()
        good = self.client.post(f"/api/v1/orders/{order.id}/activity/",
                                {"status": "in_transit", "code": "GPS_PING", "city": "Panvel"}, format="json")
        self.assertEqual(good.status_code, 201)
        order.refresh_from_db()
        self.assertEqual(order.status, "in_transit")
        bad = self.client.post(f"/api/v1/orders/{order.id}/activity/", {"status": "teleported"}, format="json")
        self.assertEqual(bad.status_code, 400)

    def test_waypoints_are_closed_when_the_order_completes(self):
        order = self.create_order()
        Waypoint.objects.create(order=order, place=self.pickup, sequence=1, waypoint_type="pickup")
        Waypoint.objects.create(order=order, place=self.dropoff, sequence=2, waypoint_type="drop")
        self.client.post(f"/api/v1/orders/{order.id}/assign/", {"driver": self.driver.id, "vehicle": self.vehicle.id}, format="json")
        self.client.post(f"/api/v1/orders/{order.id}/complete/", {"receiver_name": "Gate"}, format="json")
        self.assertEqual(order.waypoints.filter(status="pending").count(), 0)

    def test_public_tracking_is_open_and_hides_pricing(self):
        order = self.create_order()
        anonymous = APIClient()
        response = anonymous.get(f"/api/v1/track/{order.tracking_number}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["number"], order.number)
        self.assertNotIn("total_amount", response.data)
        self.assertEqual(anonymous.get("/api/v1/track/PHZ-does-not-exist/").status_code, 404)

    def test_progress_and_position_track_the_most_recent_gps_fix(self):
        order = self.create_order()
        self.assertIsNone(order.current_position())
        self.assertEqual(order.progress_percent, 0)   # freshly booked, not moving

        halfway_lat = (self.pickup.latitude + self.dropoff.latitude) / 2
        halfway_lng = (self.pickup.longitude + self.dropoff.longitude) / 2
        order.log("dispatched", "GPS_PING_1", "Midway", halfway_lat, halfway_lng, city="En route")
        order.status = "dispatched"; order.save(update_fields=["status"])
        self.assertAlmostEqual(order.progress_percent, 50, delta=2)

        # A later fix without coordinates (a desk status change) must not shadow the GPS one.
        order.log("in_transit", "STATUS_CHANGED", "Marked in transit from the desk")
        self.assertEqual(order.current_position().code, "GPS_PING_1")

        order.status = "completed"; order.save(update_fields=["status"])
        self.assertEqual(order.progress_percent, 100)

        response = self.client.get(f"/api/v1/orders/{order.id}/")
        self.assertEqual(response.data["progress_percent"], 100)
        self.assertEqual(response.data["last_position"]["code"], "GPS_PING_1")
        self.assertEqual(float(response.data["pickup_latitude"]), float(self.pickup.latitude))

    def test_public_tracking_exposes_progress_but_not_coordinates(self):
        order = self.create_order()
        order.log("dispatched", "GPS_PING_1", "Midway", self.pickup.latitude, self.pickup.longitude, city="Bhiwandi")
        order.status = "dispatched"; order.save(update_fields=["status"])
        response = APIClient().get(f"/api/v1/track/{order.tracking_number}/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("progress_percent", response.data)
        self.assertEqual(response.data["last_position"]["city"], "Bhiwandi")
        self.assertNotIn("latitude", response.data["last_position"])

    def test_order_list_supports_filtering_and_search(self):
        first = self.create_order()
        self.create_order(order_type="ptl")
        filtered = self.client.get("/api/v1/orders/", {"order_type": "ptl"})
        self.assertEqual(filtered.data["count"], 1)
        searched = self.client.get("/api/v1/orders/", {"search": first.tracking_number})
        self.assertEqual(searched.data["count"], 1)


class EpodWorkflowTests(OrderLifecycleTests):
    """OTP issue, driver capture, office review, and the gate they put on billing."""

    def deliver(self, order):
        self.client.post(f"/api/v1/orders/{order.id}/assign/",
                         {"driver": self.driver.id, "vehicle": self.vehicle.id}, format="json")
        self.client.post(f"/api/v1/orders/{order.id}/dispatch/")

    def test_otp_is_issued_once_and_confirms_a_clean_delivery(self):
        order = self.create_order()
        self.deliver(order)
        issued = self.client.post(f"/api/v1/orders/{order.id}/pod-request/",
                                  {"receiver_phone": "9820011223"}, format="json")
        self.assertEqual(issued.status_code, 200)
        self.assertEqual(len(issued.data["otp"]), 6)
        proof = ProofOfDelivery.objects.get(order=order)
        self.assertEqual(proof.status, "awaiting")

        # A second request refreshes the same proof rather than opening another one.
        again = self.client.post(f"/api/v1/orders/{order.id}/pod-request/", {}, format="json")
        self.assertEqual(ProofOfDelivery.objects.filter(order=order).count(), 1)

        submitted = self.client.post(f"/api/v1/orders/{order.id}/pod-submit/", {
            "receiver_name": "Store manager", "otp": again.data["otp"]}, format="json")
        self.assertEqual(submitted.status_code, 201, submitted.data)
        self.assertEqual(submitted.data["status"], "verified")
        self.assertTrue(submitted.data["otp_verified"])

    def test_a_wrong_or_expired_otp_is_refused(self):
        order = self.create_order()
        self.deliver(order)
        self.client.post(f"/api/v1/orders/{order.id}/pod-request/", {}, format="json")
        wrong = self.client.post(f"/api/v1/orders/{order.id}/pod-submit/",
                                 {"receiver_name": "Gate", "otp": "000000"}, format="json")
        self.assertEqual(wrong.status_code, 400)

        proof = ProofOfDelivery.objects.get(order=order)
        proof.otp_issued_at = timezone.now() - timedelta(hours=30)
        proof.save(update_fields=["otp_issued_at"])
        stale = self.client.post(f"/api/v1/orders/{order.id}/pod-submit/",
                                 {"receiver_name": "Gate", "otp": proof.otp}, format="json")
        self.assertEqual(stale.status_code, 400)
        self.assertIn("expired", str(stale.data).lower())

    def test_a_shortage_holds_the_capture_for_the_office(self):
        order = self.create_order()
        self.deliver(order)
        issued = self.client.post(f"/api/v1/orders/{order.id}/pod-request/", {}, format="json")
        submitted = self.client.post(f"/api/v1/orders/{order.id}/pod-submit/", {
            "receiver_name": "Store manager", "otp": issued.data["otp"], "shortage_kg": 120}, format="json")
        self.assertEqual(submitted.data["status"], "submitted")

        queue = self.client.get("/api/v1/proofs/pending/")
        self.assertEqual(queue.data["count"], 1)

        proof_id = submitted.data["id"]
        rejected = self.client.post(f"/api/v1/proofs/{proof_id}/reject/", {"reason": "Shortage not signed by the consignee"}, format="json")
        self.assertEqual(rejected.data["status"], "rejected")
        self.assertEqual(self.client.post(f"/api/v1/proofs/{proof_id}/reject/", {}, format="json").status_code, 400)

        verified = self.client.post(f"/api/v1/proofs/{proof_id}/verify/", {}, format="json")
        self.assertEqual(verified.data["status"], "verified")
        self.assertEqual(verified.data["verified_by"], "fleetadmin")
        self.assertEqual([a.code for a in order.activities.all()][0], "POD_VERIFIED")

    def test_completion_needs_a_capture_when_proof_is_required(self):
        order = self.create_order()
        self.deliver(order)
        refused = self.client.post(f"/api/v1/orders/{order.id}/complete/", {}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("ePOD", str(refused.data))

        order.pod_required = False
        order.save(update_fields=["pod_required"])
        self.assertEqual(self.client.post(f"/api/v1/orders/{order.id}/complete/", {}, format="json").status_code, 200)


class PhysicalPodCourierTests(OrderLifecycleTests):
    """The signed physical copy a consignee insists on, tracked back to the office by courier."""

    def capture(self, order):
        self.client.post(f"/api/v1/orders/{order.id}/assign/", {"driver": self.driver.id, "vehicle": self.vehicle.id}, format="json")
        self.client.post(f"/api/v1/orders/{order.id}/dispatch/")
        issued = self.client.post(f"/api/v1/orders/{order.id}/pod-request/", {}, format="json")
        submitted = self.client.post(f"/api/v1/orders/{order.id}/pod-submit/",
                                     {"receiver_name": "Store manager", "otp": issued.data["otp"]}, format="json")
        return submitted.data["id"]

    def test_dispatch_requires_a_courier_and_awb(self):
        order = self.create_order()
        proof_id = self.capture(order)
        missing = self.client.post(f"/api/v1/proofs/{proof_id}/courier-dispatch/", {"courier_name": "Blue Dart"}, format="json")
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(ProofOfDelivery.objects.get(pk=proof_id).courier_status, "not_sent")

    def test_full_courier_round_trip(self):
        order = self.create_order()
        proof_id = self.capture(order)

        dispatched = self.client.post(f"/api/v1/proofs/{proof_id}/courier-dispatch/", {
            "courier_name": "Blue Dart", "awb_number": "BD77410238842",
            "expected_by": str(timezone.localdate() + timedelta(days=4))}, format="json")
        self.assertEqual(dispatched.status_code, 200, dispatched.data)
        self.assertEqual(dispatched.data["courier_status"], "dispatched")
        self.assertTrue(dispatched.data["physical_copy_required"])
        self.assertEqual([a.code for a in order.activities.all()][0], "POD_COURIER_DISPATCHED")

        pending = self.client.get("/api/v1/proofs/couriers-pending/")
        self.assertEqual(pending.data["count"], 1)

        transit = self.client.post(f"/api/v1/proofs/{proof_id}/courier-transit/", {}, format="json")
        self.assertEqual(transit.data["courier_status"], "in_transit")

        received = self.client.post(f"/api/v1/proofs/{proof_id}/courier-received/", {}, format="json")
        self.assertEqual(received.status_code, 200)
        self.assertEqual(received.data["courier_status"], "delivered")
        self.assertIsNotNone(received.data["courier_received_at"])
        self.assertEqual([a.code for a in order.activities.all()][0], "POD_COURIER_RECEIVED")

        # Once received it drops off the pending queue.
        self.assertEqual(self.client.get("/api/v1/proofs/couriers-pending/").data["count"], 0)

    def test_a_copy_can_be_reported_lost(self):
        order = self.create_order()
        proof_id = self.capture(order)
        self.client.post(f"/api/v1/proofs/{proof_id}/courier-dispatch/",
                         {"courier_name": "DTDC", "awb_number": "DT99201847756"}, format="json")
        lost = self.client.post(f"/api/v1/proofs/{proof_id}/courier-lost/", {"remarks": "Consignment misplaced at the hub"}, format="json")
        self.assertEqual(lost.data["courier_status"], "lost")
        self.assertEqual([a.code for a in order.activities.all()][0], "POD_COURIER_LOST")
        # Lost is not resolved - it still needs a human decision, so it stays on the watchlist.
        self.assertEqual(self.client.get("/api/v1/proofs/couriers-pending/").data["count"], 1)

    def test_transit_and_receipt_need_a_prior_dispatch(self):
        order = self.create_order()
        proof_id = self.capture(order)
        self.assertEqual(self.client.post(f"/api/v1/proofs/{proof_id}/courier-transit/", {}, format="json").status_code, 400)
        self.assertEqual(self.client.post(f"/api/v1/proofs/{proof_id}/courier-received/", {}, format="json").status_code, 400)
        self.assertEqual(self.client.post(f"/api/v1/proofs/{proof_id}/courier-lost/", {}, format="json").status_code, 400)

    def test_overdue_flag_only_applies_while_still_out(self):
        proof = ProofOfDelivery.objects.create(order=self.create_order())
        proof.dispatch_by_courier("India Post", "IP4471023", expected_by=timezone.localdate() - timedelta(days=2))
        self.assertTrue(proof.courier_overdue)
        proof.receive_from_courier()
        self.assertFalse(proof.courier_overdue)   # back at the office, lateness no longer matters


class AutomaticInvoiceTests(OrderLifecycleTests):
    """The bill is derived from the consignment, never typed."""

    def setUp(self):
        super().setUp()
        call_command("seed_accounting")

    def deliver(self, order, **capture):
        self.client.post(f"/api/v1/orders/{order.id}/assign/",
                         {"driver": self.driver.id, "vehicle": self.vehicle.id}, format="json")
        self.client.post(f"/api/v1/orders/{order.id}/dispatch/")
        issued = self.client.post(f"/api/v1/orders/{order.id}/pod-request/", {}, format="json")
        payload = {"receiver_name": "Store manager", "otp": issued.data["otp"], **capture}
        self.client.post(f"/api/v1/orders/{order.id}/pod-submit/", payload, format="json")
        return self.client.post(f"/api/v1/orders/{order.id}/complete/", {}, format="json")

    def test_invoice_takes_freight_and_gst_from_the_rate_card(self):
        order = self.create_order()
        self.deliver(order)
        response = self.client.post(f"/api/v1/orders/{order.id}/invoice/", {}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        order.refresh_from_db()
        invoice = response.data["invoice"]
        self.assertEqual(float(invoice["freight_amount"]), float(order.freight_amount))
        self.assertEqual(float(invoice["tax_amount"]), float(order.tax_amount))
        # The bill is the rate card priced over this lane, to the paisa.
        self.assertEqual(float(invoice["total_amount"]),
                         self.rate.quote(distance_km=order.distance_km, weight_kg=order.weight_kg)["total"])
        self.assertEqual(float(invoice["total_amount"]), float(order.total_amount))
        self.assertEqual(float(invoice["gst_percent"]), 5.0)
        self.assertEqual(invoice["place_of_supply"], "Maharashtra")
        self.assertEqual(invoice["order_number"], order.number)
        self.assertTrue(response.data["journal_entry"]["number"].startswith("JV-"))

    def test_billing_the_same_consignment_twice_returns_the_same_invoice(self):
        order = self.create_order()
        self.deliver(order)
        first = self.client.post(f"/api/v1/orders/{order.id}/invoice/", {}, format="json")
        second = self.client.post(f"/api/v1/orders/{order.id}/invoice/", {}, format="json")
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.data["created"])
        self.assertEqual(first.data["invoice"]["number"], second.data["invoice"]["number"])
        self.assertEqual(Invoice.objects.filter(order=order).count(), 1)
        self.assertEqual(JournalEntry.objects.filter(reference_type="invoice").count(), 1)

    def test_an_undelivered_or_unverified_consignment_cannot_be_billed(self):
        order = self.create_order()
        early = self.client.post(f"/api/v1/orders/{order.id}/invoice/", {}, format="json")
        self.assertEqual(early.status_code, 400)
        self.assertIn("delivered", str(early.data))

        self.deliver(order, shortage_kg=90)         # held for review, so it stays unbillable
        held = self.client.post(f"/api/v1/orders/{order.id}/invoice/", {}, format="json")
        self.assertEqual(held.status_code, 400)
        self.assertIn("ePOD", str(held.data))

        proof = ProofOfDelivery.objects.get(order=order)
        self.client.post(f"/api/v1/proofs/{proof.id}/verify/", {}, format="json")
        self.assertEqual(self.client.post(f"/api/v1/orders/{order.id}/invoice/", {}, format="json").status_code, 201)

    def test_the_total_is_always_the_sum_of_its_parts(self):
        invoice = Invoice.objects.create(number="INV-MANUAL-1", customer=self.customer, freight_amount=10000,
                                         additional_charges=500, tax_amount=525, total_amount=1,
                                         due_date=timezone.localdate())
        self.assertEqual(invoice.total_amount, Decimal("11025.00"))


class VendorHireTests(AutomaticInvoiceTests):
    """Outside-sourced trips: the commercial terms, the payable, the bill and the
    four-sided settlement sheet."""

    def setUp(self):
        super().setUp()
        self.vendor = Vendor.objects.create(name="Anand Roadlines", code="VN-ANAND", email="ops@anandroadlines.example",
                                            tds_percent=Decimal("2"))

    def create_hire(self, order, **overrides):
        payload = {"order": order.id, "vendor": self.vendor.id, "hire_type": "spot", "rate_basis": "trip",
                  "agreed_rate": "12000", "outside_vehicle_number": "MH 12 AB 4455", "outside_vehicle_type": "20 ft SXL",
                  "driver_name": "Suresh Patil", "driver_phone": "9812345670", "advance_amount": "3000"}
        payload.update(overrides)
        response = self.client.post("/api/v1/hires/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return VehicleHire.objects.get(pk=response.data["id"])

    def test_payable_breakdown_nets_advance_and_tds_off_the_agreed_rate(self):
        order = self.create_order()
        hire = self.create_hire(order, loading_charge="800", unloading_charge="800")
        response = self.client.get(f"/api/v1/hires/{hire.id}/payable/")
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data["gross_amount"], 13600.0)          # 12000 + 800 + 800
        self.assertEqual(data["tds_amount"], 272.0)              # 2% of the gross
        self.assertEqual(data["taxable_amount"], 13600.0)        # no deductions agreed
        self.assertEqual(data["total_payable"], 13328.0)         # taxable - tds
        self.assertEqual(data["balance_due"], 10328.0)           # total payable - the 3000 advance

    def test_km_basis_multiplies_the_agreed_rate_by_the_orders_distance(self):
        order = self.create_order()
        hire = self.create_hire(order, rate_basis="km", agreed_rate="40", advance_amount="0")
        response = self.client.get(f"/api/v1/hires/{hire.id}/payable/")
        self.assertAlmostEqual(response.data["base_amount"], float(order.distance_km) * 40, places=2)

    def test_raise_bill_creates_a_vendor_bill_and_posts_to_the_ledger(self):
        order = self.create_order()
        hire = self.create_hire(order)
        first = self.client.post(f"/api/v1/hires/{hire.id}/raise-bill/", {}, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        self.assertTrue(first.data["journal_entry"]["number"].startswith("JV-"))
        hire.refresh_from_db()
        self.assertEqual(hire.status, "billed")

        second = self.client.post(f"/api/v1/hires/{hire.id}/raise-bill/", {}, format="json")
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.data["created"])
        self.assertEqual(second.data["bill_number"], first.data["bill_number"])

    def test_send_confirmation_records_an_outbound_message(self):
        order = self.create_order()
        hire = self.create_hire(order)
        response = self.client.post(f"/api/v1/hires/{hire.id}/send-confirmation/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["to"], "ops@anandroadlines.example")
        message = OutboundMessage.objects.get(pk=response.data["message_id"])
        self.assertIn(order.number, message.body)
        self.assertIn("12000", message.body)

    def test_send_confirmation_requires_a_vendor_email(self):
        order = self.create_order()
        no_email_vendor = Vendor.objects.create(name="No Email Transport", code="VN-NOMAIL")
        hire = self.create_hire(order, vendor=no_email_vendor.id)
        response = self.client.post(f"/api/v1/hires/{hire.id}/send-confirmation/", {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_order_settlement_combines_customer_vendor_and_vehicle_sides(self):
        order = self.create_order()
        hire = self.create_hire(order)
        self.deliver(order)
        order.refresh_from_db()
        self.client.post(f"/api/v1/orders/{order.id}/invoice/", {}, format="json")
        self.client.post(f"/api/v1/hires/{hire.id}/raise-bill/", {}, format="json")
        FuelEntry.objects.create(vehicle=self.vehicle, trip=order.trip, odometer_km=self.vehicle.current_odometer_km + 150,
                                 volume_litres=Decimal("40"), rate_per_litre=Decimal("95"))

        response = self.client.get(f"/api/v1/orders/{order.id}/settlement/")
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data["customer"]["total_amount"], float(order.total_amount))
        self.assertEqual(data["vendor"]["vendor"], "Anand Roadlines")
        self.assertEqual(data["vendor"]["bill_status"], "raised")
        self.assertEqual(data["vehicle"]["fuel"], 3800.0)
        self.assertAlmostEqual(data["total_cost"], 12000.0 + 3800.0, places=2)


class AllocationTests(BaseFleetOpsTest):
    """Recommending and confirming a vehicle for an order - own capacity, vendor
    capacity, and the spot-hire estimate when a vendor has no vehicle on file yet."""

    def create_order(self, **overrides):
        payload = {"customer": self.customer.id, "pickup": self.pickup.id, "dropoff": self.dropoff.id,
                  "service_rate": self.rate.id, "order_type": "ftl", "weight_kg": 12400,
                  "payload_description": "Packaged food cartons", "packages": 480}
        payload.update(overrides)
        response = self.client.post("/api/v1/orders/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return Order.objects.get(pk=response.data["id"])

    def test_recommend_vehicles_includes_the_available_own_vehicle(self):
        order = self.create_order()
        response = self.client.post(f"/api/v1/orders/{order.id}/recommend-vehicles/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        own = [c for c in response.data["candidates"] if c["vehicle_id"] == self.vehicle.id]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["source"], "own")
        self.assertGreater(own[0]["expected_revenue"], 0)

    def test_recommend_vehicles_offers_a_spot_hire_estimate_for_an_active_vendor(self):
        order = self.create_order()
        Vendor.objects.create(name="Anand Roadlines", code="VN-ANAND", vendor_type="transporter", status="active")
        response = self.client.post(f"/api/v1/orders/{order.id}/recommend-vehicles/", {}, format="json")
        spot = [c for c in response.data["candidates"] if c["source"] == "vendor_spot"]
        self.assertEqual(len(spot), 1)
        self.assertTrue(spot[0]["estimated_cost"])

    def test_recommend_vehicles_ranks_by_expected_profit_not_distance(self):
        order = self.create_order()
        candidates = self.client.post(f"/api/v1/orders/{order.id}/recommend-vehicles/", {}, format="json").data["candidates"]
        profits = [c["expected_profit"] for c in candidates]
        self.assertEqual(profits, sorted(profits, reverse=True))
        self.assertTrue(candidates[0]["recommended"])

    def test_confirm_vehicle_with_an_own_vehicle_and_driver_opens_a_trip(self):
        order = self.create_order()
        response = self.client.post(f"/api/v1/orders/{order.id}/confirm-vehicle/",
                                    {"vehicle": self.vehicle.id, "driver": self.driver.id}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNotNone(response.data["order"]["trip"])
        self.assertIsNone(response.data["hire"])
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.status, "allocated")
        self.assertEqual(self.vehicle.current_trip_id, response.data["order"]["trip"])

    def test_confirm_vehicle_for_an_outside_vehicle_creates_a_hire_and_sends_confirmation(self):
        order = self.create_order()
        vendor = Vendor.objects.create(name="Anand Roadlines", code="VN-ANAND", email="ops@anandroadlines.example")
        response = self.client.post(f"/api/v1/orders/{order.id}/confirm-vehicle/", {
            "vendor": vendor.id,
            "outside_vehicle": {"vehicle_number": "MH 12 AB 4455", "vehicle_type": "20 ft SXL", "capacity_kg": 9000},
            "hire": {"agreed_rate": "12000", "rate_basis": "trip", "driver_name": "Suresh Patil",
                    "driver_phone": "9812345670", "advance_amount": "3000"},
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNotNone(response.data["hire"])
        self.assertEqual(response.data["hire"]["status"], "confirmed")
        self.assertTrue(response.data["vendor_confirmation_sent"])
        hired_vehicle = Vehicle.objects.get(registration_number="MH 12 AB 4455")
        self.assertEqual(hired_vehicle.ownership, "outside")
        self.assertEqual(hired_vehicle.vendor_id, vendor.id)
        # No driver was supplied - the vendor's own driver lives on the hire, not the
        # internal driver master, so no trip has opened yet.
        self.assertIsNone(response.data["order"]["trip"])
        message = OutboundMessage.objects.get(reference_type="hire", reference_id=str(response.data["hire"]["id"]))
        self.assertEqual(message.status, "sent")

    def test_confirm_vehicle_flags_an_expired_document(self):
        order = self.create_order()
        ComplianceDocument.objects.create(vehicle=self.vehicle, document_type="insurance",
                                          expiry_date=timezone.localdate() - timedelta(days=5))
        response = self.client.post(f"/api/v1/orders/{order.id}/confirm-vehicle/",
                                    {"vehicle": self.vehicle.id, "driver": self.driver.id}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("expired" in warning for warning in response.data["warnings"]))

    def test_availability_endpoint_filters_by_radius_and_sorts_by_distance(self):
        near = Vehicle.objects.create(registration_number="MH 04 AA 1111", vehicle_type="32 ft MXL",
                                      current_latitude=Decimal("19.300000"), current_longitude=Decimal("73.060000"))
        far = Vehicle.objects.create(registration_number="MH 04 BB 2222", vehicle_type="32 ft MXL",
                                     current_latitude=Decimal("21.000000"), current_longitude=Decimal("79.000000"))
        response = self.client.get("/api/v1/vehicles/availability/", {"place": self.pickup.id, "radius_km": 50})
        ids = [row["id"] for row in response.data["vehicles"]]
        self.assertIn(near.id, ids)
        self.assertNotIn(far.id, ids)
        self.assertEqual(response.data["vehicles"][0]["id"], near.id)


class LaneProjectionTests(BaseFleetOpsTest):
    """What a lane earns against what this fleet actually spends to run it."""

    def record_history(self):
        FuelEntry.objects.create(vehicle=self.vehicle, odometer_km=268400, volume_litres=Decimal("250"),
                                 rate_per_litre=Decimal("90.00"))
        FuelEntry.objects.create(vehicle=self.vehicle, odometer_km=269400, volume_litres=Decimal("250"),
                                 rate_per_litre=Decimal("90.00"))       # 1000 km on 250 litres -> 4 km/l
        TripExpense.objects.create(vehicle=self.vehicle, category="toll", amount=4000)

    def test_projection_uses_recorded_diesel_and_on_road_spend(self):
        self.record_history()
        response = self.client.post("/api/v1/service-rates/project/", {
            "service_rate": self.rate.id, "distance_km": 150, "weight_kg": 12400, "trips_per_month": 20}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertTrue(data["basis"]["from_history"])
        self.assertEqual(data["basis"]["mileage_kmpl"], 4.0)
        self.assertEqual(data["basis"]["diesel_price"], 90.0)
        self.assertEqual(data["fuel_cost"], 3375.0)                     # 150 km at 22.50/km
        self.assertEqual(data["on_road_cost"], 600.0)                   # 4000 over 1000 km, 150 km of it
        self.assertEqual(data["total_cost"], 3975.0)
        self.assertEqual(data["revenue"], 13339.5)                      # taxable value, GST excluded
        self.assertEqual(data["margin"], 9364.5)
        self.assertEqual(data["monthly"]["margin"], 187290.0)
        self.assertEqual(data["break_even_rate_per_km"], 26.5)

    def test_overrides_beat_history_and_history_is_optional(self):
        cold = self.client.post("/api/v1/service-rates/project/", {
            "service_rate": self.rate.id, "distance_km": 100}, format="json")
        self.assertFalse(cold.data["basis"]["from_history"])
        self.assertEqual(cold.data["on_road_cost"], 0.0)

        self.record_history()
        overridden = self.client.post("/api/v1/service-rates/project/", {
            "service_rate": self.rate.id, "distance_km": 150, "diesel_price": "100.00", "mileage_kmpl": "5.00"}, format="json")
        self.assertEqual(overridden.data["basis"]["mileage_kmpl"], 5.0)
        self.assertEqual(overridden.data["fuel_cost"], 3000.0)          # 150 km at 20/km

    def test_a_projection_needs_a_rate_card(self):
        self.assertEqual(self.client.post("/api/v1/service-rates/project/", {"distance_km": 150}, format="json").status_code, 400)


class FuelAndExpenseTests(BaseFleetOpsTest):
    def test_fuel_entry_computes_amount_mileage_and_updates_odometer(self):
        FuelEntry.objects.create(vehicle=self.vehicle, odometer_km=268400, volume_litres=Decimal("250"),
                                 rate_per_litre=Decimal("94.20"))
        second = FuelEntry.objects.create(vehicle=self.vehicle, odometer_km=269500, volume_litres=Decimal("250"),
                                          rate_per_litre=Decimal("94.20"))
        self.assertEqual(second.amount, Decimal("23550.00"))
        self.assertEqual(second.mileage_kmpl, Decimal("4.40"))    # 1100 km on 250 litres
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.current_odometer_km, 269500)

    def test_mileage_report_groups_by_vehicle(self):
        FuelEntry.objects.create(vehicle=self.vehicle, odometer_km=268400, volume_litres=Decimal("250"), rate_per_litre=Decimal("94.20"))
        FuelEntry.objects.create(vehicle=self.vehicle, odometer_km=269500, volume_litres=Decimal("250"), rate_per_litre=Decimal("94.20"))
        response = self.client.get("/api/v1/fuel-entries/mileage/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["vehicle"], "MH 04 JU 9182")
        self.assertEqual(response.data[0]["fills"], 2)

    def test_expense_summary_and_approval(self):
        toll = TripExpense.objects.create(vehicle=self.vehicle, category="toll", amount=3200)
        TripExpense.objects.create(vehicle=self.vehicle, category="driver_allowance", amount=1800)
        summary = self.client.get("/api/v1/trip-expenses/summary/")
        self.assertEqual(summary.data[0], {"category": "toll", "total": 3200.0, "entries": 1})
        approved = self.client.post(f"/api/v1/trip-expenses/{toll.id}/approve/")
        self.assertEqual(approved.data["status"], "approved")


class ComplianceTests(BaseFleetOpsTest):
    def test_document_status_reflects_expiry_window(self):
        today = timezone.localdate()
        valid = ComplianceDocument.objects.create(vehicle=self.vehicle, document_type="rc", expiry_date=today + timedelta(days=400))
        expiring = ComplianceDocument.objects.create(vehicle=self.vehicle, document_type="puc", expiry_date=today + timedelta(days=10))
        expired = ComplianceDocument.objects.create(vehicle=self.vehicle, document_type="fitness", expiry_date=today - timedelta(days=2))
        self.assertEqual((valid.status, expiring.status, expired.status), ("valid", "expiring", "expired"))
        self.assertEqual(expired.days_to_expiry, -2)

    def test_expiring_endpoint_lists_documents_needing_renewal(self):
        today = timezone.localdate()
        ComplianceDocument.objects.create(vehicle=self.vehicle, document_type="puc", expiry_date=today + timedelta(days=10))
        ComplianceDocument.objects.create(vehicle=self.vehicle, document_type="rc", expiry_date=today + timedelta(days=400))
        response = self.client.get("/api/v1/compliance-documents/expiring/", {"days": 30})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["documents"][0]["document_type"], "puc")

    def test_document_must_belong_to_a_vehicle_or_driver(self):
        response = self.client.post("/api/v1/compliance-documents/", {"document_type": "insurance", "number": "X"}, format="json")
        self.assertEqual(response.status_code, 400)


class MaintenanceScheduleTests(BaseFleetOpsTest):
    def test_next_due_is_derived_from_the_interval(self):
        schedule = MaintenanceSchedule.objects.create(vehicle=self.vehicle, task="Engine oil", interval_km=20000,
                                                      interval_days=180, last_service_km=250000,
                                                      last_service_date=timezone.localdate() - timedelta(days=200))
        self.assertEqual(schedule.next_due_km, 270000)
        self.assertEqual(schedule.km_remaining, 1600)
        self.assertTrue(schedule.is_due)                          # calendar interval already lapsed

    def test_completing_a_service_rolls_the_schedule_forward(self):
        schedule = MaintenanceSchedule.objects.create(vehicle=self.vehicle, task="Tyre rotation", interval_km=15000,
                                                      last_service_km=250000)
        response = self.client.post(f"/api/v1/maintenance-schedules/{schedule.id}/complete/", {"odometer_km": 268400}, format="json")
        self.assertEqual(response.status_code, 200)
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_due_km, 283400)


class FleetAndIssueTests(BaseFleetOpsTest):
    def test_fleet_assignment_adds_and_removes_members(self):
        fleet = Fleet.objects.create(name="West India owned fleet", code="FL-WEST", service_area=self.area)
        added = self.client.post(f"/api/v1/fleets/{fleet.id}/assign/",
                                 {"vehicles": [self.vehicle.id], "drivers": [self.driver.id]}, format="json")
        self.assertEqual((added.data["vehicle_count"], added.data["driver_count"]), (1, 1))
        removed = self.client.post(f"/api/v1/fleets/{fleet.id}/assign/", {"vehicles": [self.vehicle.id], "remove": True}, format="json")
        self.assertEqual(removed.data["vehicle_count"], 0)

    def test_issue_number_is_generated_and_resolvable(self):
        created = self.client.post("/api/v1/issues/", {"vehicle": self.vehicle.id, "issue_type": "tyre",
                                                       "priority": "high", "description": "Sidewall cut"}, format="json")
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.data["number"].startswith("ISS-"))
        resolved = self.client.post(f"/api/v1/issues/{created.data['id']}/resolve/", {"resolution": "Tyre replaced"}, format="json")
        self.assertEqual(resolved.data["status"], "resolved")
        self.assertIsNotNone(Issue.objects.get(pk=created.data["id"]).resolved_at)


class AnalyticsAndAuthTests(BaseFleetOpsTest):
    def test_fleet_analytics_reports_cost_per_km_and_alerts(self):
        FuelEntry.objects.create(vehicle=self.vehicle, odometer_km=268400, volume_litres=Decimal("250"), rate_per_litre=Decimal("94.20"))
        FuelEntry.objects.create(vehicle=self.vehicle, odometer_km=269500, volume_litres=Decimal("250"), rate_per_litre=Decimal("94.20"))
        TripExpense.objects.create(vehicle=self.vehicle, category="toll", amount=3200)
        response = self.client.get("/api/v1/analytics/fleet/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["fleet_size"], 1)
        self.assertEqual(response.data["average_mileage_kmpl"], 4.4)
        self.assertGreater(response.data["cost_per_km"], 0)
        self.assertIn("expense_split", response.data)

    def test_dashboard_exposes_fleetops_counters(self):
        response = self.client.get("/api/v1/dashboard/")
        for key in ("orders", "active_orders", "fleets", "vendors", "zones", "open_issues", "documents_expiring"):
            self.assertIn(key, response.data)

    def test_fleetops_endpoints_require_authentication(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get("/api/v1/orders/").status_code, 401)
        self.assertEqual(anonymous.get("/api/v1/analytics/fleet/").status_code, 401)

    def test_trip_endpoints_still_work_alongside_the_dispatch_action(self):
        response = self.client.get("/api/v1/trips/")
        self.assertEqual(response.status_code, 200)


class VehicleStatusAndOwnershipTests(BaseFleetOpsTest):
    def test_dispatch_and_complete_write_a_status_log(self):
        order = OrderLifecycleTests.create_order(self)
        self.client.post(f"/api/v1/orders/{order.id}/assign/",
                         {"driver": self.driver.id, "vehicle": self.vehicle.id}, format="json")
        self.client.post(f"/api/v1/orders/{order.id}/dispatch/")
        history = self.client.get(f"/api/v1/vehicles/{self.vehicle.id}/status-history/")
        self.assertEqual(history.status_code, 200)
        statuses = [row["status"] for row in history.data]
        self.assertIn("running", statuses)
        self.assertIn("allocated", statuses)
        # The closed row for "allocated" should have an end time once "running" opened.
        allocated_row = next(row for row in history.data if row["status"] == "allocated")
        self.assertIsNotNone(allocated_row["ended_at"])

    def test_set_status_endpoint_moves_a_vehicle_to_breakdown(self):
        response = self.client.post(f"/api/v1/vehicles/{self.vehicle.id}/set-status/",
                                    {"status": "breakdown", "reason": "Clutch failure near Lonavala"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.status, "breakdown")

    def test_set_status_rejects_an_unknown_status(self):
        response = self.client.post(f"/api/v1/vehicles/{self.vehicle.id}/set-status/", {"status": "on_fire"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_ownership_defaults_to_own_and_accepts_outside_with_a_vendor(self):
        self.assertEqual(self.vehicle.ownership, "own")
        vendor = Vendor.objects.create(name="Anand Roadlines", code="VN-ANAND")
        hired = Vehicle.objects.create(registration_number="MH 12 AB 4455", vehicle_type="20 ft SXL",
                                       ownership="outside", vendor=vendor)
        self.assertEqual(hired.vendor_id, vendor.id)


class PaginationTests(BaseFleetOpsTest):
    def test_list_reports_the_true_total_not_the_page_size(self):
        for index in range(60):
            Vendor.objects.create(name=f"Vendor {index}", code=f"VN-{index:03d}")
        default_page = self.client.get("/api/v1/vendors/")
        self.assertEqual(default_page.data["count"], 60)          # the real total
        self.assertEqual(len(default_page.data["results"]), 50)   # one page of it

    def test_clients_can_request_a_larger_page_up_to_the_cap(self):
        for index in range(60):
            Vendor.objects.create(name=f"Vendor {index}", code=f"VN-{index:03d}")
        full = self.client.get("/api/v1/vendors/", {"page_size": 500})
        self.assertEqual(len(full.data["results"]), 60)
        capped = self.client.get("/api/v1/vendors/", {"page_size": 5000})
        self.assertEqual(len(capped.data["results"]), 60)         # cap applies, request still succeeds


class LegacyModuleCreationTests(BaseFleetOpsTest):
    """The console create forms once posted hardcoded foreign keys such as `customer: 1`,
    which fail on any database where that row does not exist. These cover the real payloads."""

    def test_trip_can_be_opened_before_consignments_are_attached(self):
        response = self.client.post("/api/v1/trips/", {
            "number": "TRP-0001", "vehicle": self.vehicle.id, "driver": self.driver.id,
            "origin": "Bhiwandi", "destination": "Chakan",
            "planned_departure": "2026-08-05T14:30:00Z", "estimated_cost": 31600}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["lorry_receipts"], [])

    def test_trip_accepts_consignments_when_supplied(self):
        receipt = self.client.post("/api/v1/lorry-receipts/", {
            "number": "LR-0001", "customer": self.customer.id, "consignor": "Tata", "consignee": "D-Mart",
            "origin": "Bhiwandi", "destination": "Chakan", "material": "Food", "weight_kg": 12400}, format="json")
        self.assertEqual(receipt.status_code, 201, receipt.data)
        response = self.client.post("/api/v1/trips/", {
            "number": "TRP-0002", "vehicle": self.vehicle.id, "driver": self.driver.id,
            "origin": "Bhiwandi", "destination": "Chakan", "planned_departure": "2026-08-05T14:30:00Z",
            "lorry_receipts": [receipt.data["id"]]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["lorry_receipts"], [receipt.data["id"]])

    def test_customer_keeps_the_gstin_the_operator_typed(self):
        response = self.client.post("/api/v1/customers/", {
            "name": "Asian Paints Ltd", "gstin": "27AAACA3622K1ZV", "pan": "AAACA3622K"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["gstin"], "27AAACA3622K1ZV")


class TripSettlementTests(BaseFleetOpsTest):
    """The trip-sheet numbers a transport office fills in by hand, checked against a
    real trip from the operator's own settlement register: Surat to Delhi-Noida,
    frozen load, freight 96,800, closing at a 5,510 shortfall against the diesel
    advance."""

    def create_trip(self):
        response = self.client.post("/api/v1/trips/", {
            "number": "TRP-SURAT-NOIDA", "vehicle": self.vehicle.id, "driver": self.driver.id,
            "origin": "Surat", "destination": "Delhi-Noida", "planned_departure": "2026-07-05T08:00:00Z"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return Trip.objects.get(pk=response.data["id"])

    def test_settlement_post_computes_the_same_totals_as_the_paper_register(self):
        trip = self.create_trip()
        response = self.client.post(f"/api/v1/trips/{trip.id}/settlement/", {
            "load_type": "frozen", "load_date": "2026-07-05", "unload_date": "2026-07-08",
            "passed_km": 1200, "start_odometer_km": 268400, "end_odometer_km": 269604,
            "freight_amount": "96800", "diesel_given": "39000",
            "expenses": {"diesel": "26400", "halting": "2500", "loading": "300",
                        "police": "1200", "parking": "300", "cash_toll": "390", "salary": "2400"},
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        summary = response.data["summary"]
        self.assertEqual(summary["total_exp"], 33490.0)          # sum of every expense line
        self.assertEqual(summary["diesel_given"], 39000.0)
        self.assertEqual(summary["difference"], -5510.0)         # spent less than the advance
        self.assertEqual(summary["running_km"], 1204)            # 216733 - 215529
        self.assertEqual(summary["per_km_exp"], 27.91)            # 33490 / 1200 passed km
        self.assertEqual(summary["per_km_rev"], 80.67)            # 96800 / 1200 passed km
        self.assertEqual(summary["trip_profit"], 63310.0)         # 96800 - 33490
        self.assertEqual(TripExpense.objects.filter(trip=trip).count(), 7)
        self.assertEqual(response.data["expenses"]["diesel"], 26400.0)
        self.assertEqual(response.data["expenses"]["salary"], 2400.0)

        trip.refresh_from_db()
        self.assertEqual(trip.load_type, "frozen")
        self.assertEqual(str(trip.load_date), "2026-07-05")
        self.assertEqual(trip.advance_amount, Decimal("39000.00"))
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.current_odometer_km, 269604)   # rolled forward, like a fuel entry

    def test_resubmitting_the_settlement_updates_lines_rather_than_duplicating(self):
        trip = self.create_trip()
        self.client.post(f"/api/v1/trips/{trip.id}/settlement/",
                         {"expenses": {"diesel": "26400", "parking": "300"}}, format="json")
        self.client.post(f"/api/v1/trips/{trip.id}/settlement/",
                         {"expenses": {"diesel": "27000", "parking": "0"}}, format="json")
        self.assertEqual(TripExpense.objects.filter(trip=trip).count(), 1)   # parking cleared, not left stale
        self.assertEqual(TripExpense.objects.get(trip=trip, category="diesel").amount, Decimal("27000.00"))

    def test_settlement_rejects_an_unknown_expense_category(self):
        trip = self.create_trip()
        response = self.client.post(f"/api/v1/trips/{trip.id}/settlement/",
                                    {"expenses": {"not_a_real_category": "100"}}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_get_settlement_reflects_a_linked_orders_freight(self):
        """A trip created from an order prices its settlement off that order,
        not a manually typed freight figure."""
        order_response = self.client.post("/api/v1/orders/", {
            "customer": self.customer.id, "pickup": self.pickup.id, "dropoff": self.dropoff.id,
            "service_rate": self.rate.id, "weight_kg": 12400}, format="json")
        order = Order.objects.get(pk=order_response.data["id"])
        self.client.post(f"/api/v1/orders/{order.id}/assign/",
                         {"driver": self.driver.id, "vehicle": self.vehicle.id}, format="json")
        order.refresh_from_db()
        self.client.post(f"/api/v1/trips/{order.trip_id}/settlement/",
                         {"passed_km": 100, "expenses": {"diesel": "2000"}}, format="json")
        response = self.client.get(f"/api/v1/trips/{order.trip_id}/settlement/")
        self.assertEqual(response.data["summary"]["freight"], float(order.total_amount))

    def test_vehicle_keeps_the_registration_the_operator_typed(self):
        response = self.client.post("/api/v1/vehicles/", {
            "registration_number": "MH 12 PQ 4407", "vehicle_type": "22 ft SXL", "capacity_kg": 9000}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["registration_number"], "MH 12 PQ 4407")

    def test_service_area_and_zone_can_be_created_on_an_empty_database(self):
        area = self.client.post("/api/v1/service-areas/", {"name": "North India", "code": "NORTH"}, format="json")
        self.assertEqual(area.status_code, 201, area.data)
        zone = self.client.post("/api/v1/zones/", {
            "service_area": area.data["id"], "name": "Delhi NCR",
            "center_latitude": "28.613900", "center_longitude": "77.209000", "radius_km": 60}, format="json")
        self.assertEqual(zone.status_code, 201, zone.data)


class GeotrackersParsingTests(TestCase):
    """The vendor feed is undocumented, so the parser is pinned against every
    envelope and field spelling seen so far rather than against one schema."""

    def test_finds_vehicles_nested_two_levels_deep(self):
        # The shape the first cut missed entirely: the array is not top level,
        # and `data` is an object rather than the list itself.
        payload = {"status": "ok", "data": {"vehicleList": [
            {"vehicleNo": "MH04JU9182", "lat": 19.2967, "lng": 73.0631, "speed": 42}]}}
        pings = geotrackers.parse(payload)
        self.assertEqual(len(pings), 1)
        self.assertEqual(pings[0]["reg"], "MH04JU9182")
        self.assertAlmostEqual(pings[0]["lat"], 19.2967)
        self.assertEqual(pings[0]["gps_status"], "moving")

    def test_reads_longitude_written_as_lon(self):
        # `_f("lng", "lng", ...)` used to drop this on the floor and return 0.
        pings = geotrackers.parse([{"regNo": "MH04JU9182", "latitude": 19.2967, "lon": 73.0631}])
        self.assertAlmostEqual(pings[0]["lng"], 73.0631)
        self.assertTrue(pings[0]["in_india"])

    def test_transposed_coordinates_are_put_back_the_right_way_round(self):
        pings = geotrackers.parse([{"vehicleNo": "MH04JU9182", "lat": 73.0631, "lng": 19.2967}])
        self.assertAlmostEqual(pings[0]["lat"], 19.2967)
        self.assertAlmostEqual(pings[0]["lng"], 73.0631)
        self.assertTrue(pings[0]["coords_swapped"])
        self.assertTrue(pings[0]["in_india"])

    def test_string_coordinates_with_a_hemisphere_suffix(self):
        pings = geotrackers.parse([{"vehicle_no": "MH04JU9182",
                                    "Latitude": "19.2967 N", "Longitude": "73.0631 E", "Speed": "0"}])
        self.assertAlmostEqual(pings[0]["lat"], 19.2967)
        self.assertAlmostEqual(pings[0]["lng"], 73.0631)
        self.assertEqual(pings[0]["gps_status"], "parked")

    def test_coordinates_nested_under_a_position_object(self):
        pings = geotrackers.parse({"result": [
            {"assetName": "MH04JU9182", "position": {"lat": 19.2967, "lng": 73.0631}, "ignition": "1"}]})
        self.assertAlmostEqual(pings[0]["lat"], 19.2967)
        self.assertEqual(pings[0]["gps_status"], "idle")   # ignition on, not moving

    def test_null_island_is_treated_as_no_fix(self):
        pings = geotrackers.parse([{"vehicleNo": "MH04JU9182", "lat": 0, "lng": 0}])
        self.assertIsNone(pings[0]["lat"])
        self.assertFalse(pings[0]["in_india"])

    def test_vendor_movement_state_wins_over_speed(self):
        pings = geotrackers.parse([{"vehicleNo": "X", "lat": 19.2, "lng": 73.0,
                                    "speed": 0, "status": "Moving"}])
        self.assertEqual(pings[0]["gps_status"], "moving")

    def test_registration_matching_ignores_spacing(self):
        self.assertEqual(geotrackers.normalise_registration("MH 04 JU 9182"),
                         geotrackers.normalise_registration("mh-04-ju-9182"))

    def test_an_unparseable_payload_yields_no_vehicles_rather_than_raising(self):
        self.assertEqual(geotrackers.parse({"message": "no data"}), [])
        self.assertEqual(geotrackers.parse([]), [])


class LiveTrackingEndpointTests(BaseFleetOpsTest):
    def test_positions_are_written_back_onto_matching_vehicles(self):
        # self.vehicle is "MH 04 JU 9182"; the feed sends it unspaced, which is
        # the whole point of normalising before the lookup.
        vehicle = self.vehicle
        feed = {"data": {"vehicleList": [
            {"vehicleNo": "MH04JU9182", "lat": 19.2967, "lng": 73.0631, "speed": 41.5},
            {"vehicleNo": "UNKNOWN99", "lat": 18.5, "lng": 73.8, "speed": 0}]}}
        with mock.patch.object(geotrackers, "fetch_dashboard", return_value=(feed, None)):
            response = self.client.get("/api/v1/live-tracking/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 2)
        self.assertEqual(response.data["located"], 2)
        # The device with no FMS row is still reported, just unmatched.
        self.assertEqual(response.data["matched"], 1)
        vehicle.refresh_from_db()
        self.assertAlmostEqual(float(vehicle.current_latitude), 19.2967, places=4)
        self.assertAlmostEqual(float(vehicle.current_longitude), 73.0631, places=4)

    def test_a_telematics_outage_reports_the_error_and_does_not_500(self):
        with mock.patch.object(geotrackers, "fetch_dashboard", return_value=(None, "HTTP 403 from Geotrackers")):
            response = self.client.get("/api/v1/live-tracking/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["vehicles"], [])
        self.assertIn("403", response.data["error"])

    def test_vehicles_without_coordinates_are_called_out_as_a_parsing_problem(self):
        feed = [{"vehicleNo": "MH04JU9182", "speed": 10}]
        with mock.patch.object(geotrackers, "fetch_dashboard", return_value=(feed, None)):
            response = self.client.get("/api/v1/live-tracking/")
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["located"], 0)
        self.assertIn("no usable coordinates", response.data["error"])

    def test_debug_mode_exposes_the_raw_shape(self):
        feed = {"data": {"vehicleList": [{"vehicleNo": "MH04JU9182", "lat": 19.2, "lng": 73.0}]}}
        with mock.patch.object(geotrackers, "fetch_dashboard", return_value=(feed, None)):
            response = self.client.get("/api/v1/live-tracking/?debug=1")
        self.assertEqual(response.data["rows_detected"], 1)
        self.assertEqual(response.data["first_row_raw"]["vehicleNo"], "MH04JU9182")
        self.assertEqual(response.data["top_level_keys"], ["data"])
