"""Tests for the Fleetbase FleetOps inspired modules."""
import base64
import json as _json
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

from .models import (ComplianceDocument, Customer, Driver, Fleet, FuelEntry, Indent, Invoice, Issue, LorryReceipt,
                     MaintenanceSchedule, Order, Place, ProofOfDelivery, ServiceArea, ServiceRate, Settlement, Trip,
                     TripExpense, Vehicle, VehicleHire, VehicleSize, VehicleType, Vendor, Waypoint, Zone,
                     haversine_km, money)

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


class VehicleMasterTests(BaseFleetOpsTest):
    def test_vehicle_size_master_can_be_created_and_listed(self):
        created = self.client.post("/api/v1/vehicle-sizes/", {
            "name": "40 ft HQ", "default_capacity_kg": 20000, "sort_order": 90, "status": "active",
        }, format="json")

        self.assertEqual(created.status_code, 201, created.data)
        listed = self.client.get("/api/v1/vehicle-sizes/")
        self.assertEqual(listed.status_code, 200)
        self.assertIn("40 ft HQ", [row["name"] for row in listed.data["results"]])
        self.assertTrue(VehicleSize.objects.filter(name="40 ft HQ").exists())

    def test_vehicle_type_master_maps_business_label_to_planner_class(self):
        created = self.client.post("/api/v1/vehicle-types/", {
            "name": "Ambient dry van", "temperature_class": "dry", "body_type": "container",
            "sort_order": 90, "status": "active",
        }, format="json")

        self.assertEqual(created.status_code, 201, created.data)
        master = VehicleType.objects.get(name="Ambient dry van")
        self.assertEqual(master.temperature_class, "dry")
        self.assertEqual(master.body_type, "container")


class IndentRequirementMappingTests(BaseFleetOpsTest):
    def test_indent_requirements_map_to_the_generated_order(self):
        required_at = timezone.now() + timedelta(hours=2)
        expected_delivery = required_at + timedelta(hours=8)
        indent = Indent.objects.create(
            number="IND-MAP-1", customer=self.customer, pickup=self.pickup, dropoff=self.dropoff,
            vehicle=self.vehicle, driver=self.driver, status="allocated", indent_type="part_load",
            delivery_access="no_entry_area", required_at=required_at,
            expected_delivery_at=expected_delivery, expected_running_km=Decimal("180"),
            vehicle_type="32 ft MXL", temperature_class="chiller", temp_min_c=Decimal("2"), temp_max_c=Decimal("8"),
            temp_tolerance_c=Decimal("1"))

        response = self.client.post(f"/api/v1/indents/{indent.id}/convert/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        indent.refresh_from_db()
        order = indent.order
        self.assertEqual(order.order_type, "ptl")
        self.assertEqual(order.delivery_access, "no_entry_area")
        self.assertEqual(order.expected_delivery_at, expected_delivery)
        self.assertEqual(order.expected_running_km, Decimal("180"))
        self.assertEqual(order.vehicle_type, "32 ft MXL")
        self.assertEqual(order.temp_min_c, Decimal("2"))
        self.assertEqual(order.temp_max_c, Decimal("8"))
        self.assertEqual(order.temp_tolerance_c, Decimal("1"))
        self.assertEqual(order.temp_set_point_c, Decimal("5"))

    def test_indent_rejects_an_inverted_temperature_range(self):
        response = self.client.post("/api/v1/indents/", {
            "customer": self.customer.id, "pickup": self.pickup.id, "dropoff": self.dropoff.id,
            "temp_min_c": "8", "temp_max_c": "2"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("temp_max_c", response.data)


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

    def test_a_verified_epod_refuses_a_second_pod_request(self):
        order = self.create_order()
        self.deliver(order)
        issued = self.client.post(f"/api/v1/orders/{order.id}/pod-request/", {}, format="json")
        self.client.post(f"/api/v1/orders/{order.id}/pod-submit/",
                         {"receiver_name": "Store manager", "otp": issued.data["otp"]}, format="json")
        self.assertEqual(ProofOfDelivery.objects.get(order=order).status, "verified")

        refused = self.client.post(f"/api/v1/orders/{order.id}/pod-request/", {}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("already verified", str(refused.data))
        self.assertEqual(ProofOfDelivery.objects.filter(order=order).count(), 1)

    def test_a_pod_awaiting_review_refuses_a_second_pod_submit(self):
        order = self.create_order()
        self.deliver(order)
        issued = self.client.post(f"/api/v1/orders/{order.id}/pod-request/", {}, format="json")
        self.client.post(f"/api/v1/orders/{order.id}/pod-submit/", {
            "receiver_name": "Store manager", "otp": issued.data["otp"], "shortage_kg": 120}, format="json")
        self.assertEqual(ProofOfDelivery.objects.get(order=order).status, "submitted")

        refused = self.client.post(f"/api/v1/orders/{order.id}/pod-submit/",
                                   {"receiver_name": "Someone else"}, format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("awaiting office review", str(refused.data))
        self.assertEqual(ProofOfDelivery.objects.filter(order=order).count(), 1)

    def test_completing_an_order_reuses_its_already_verified_epod(self):
        """The Orders board resends a placeholder receiver_name on every drag-to-
        Delivered - completing an order that already cleared ePOD on the ePOD
        screen must not try to open a second proof against it."""
        order = self.create_order()
        self.deliver(order)
        issued = self.client.post(f"/api/v1/orders/{order.id}/pod-request/", {}, format="json")
        self.client.post(f"/api/v1/orders/{order.id}/pod-submit/",
                         {"receiver_name": "Store manager", "otp": issued.data["otp"]}, format="json")
        verified_id = ProofOfDelivery.objects.get(order=order).id

        response = self.client.post(f"/api/v1/orders/{order.id}/complete/",
                                    {"receiver_name": "Consignee", "proof_type": "signature"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(ProofOfDelivery.objects.filter(order=order).count(), 1)
        self.assertEqual(ProofOfDelivery.objects.get(order=order).id, verified_id)

    def test_the_database_itself_refuses_a_second_proof_for_one_order(self):
        from django.db import IntegrityError, transaction as _tx
        order = self.create_order()
        ProofOfDelivery.objects.create(order=order)
        with self.assertRaises(IntegrityError), _tx.atomic():
            ProofOfDelivery.objects.create(order=order)


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
            "outside_vehicle": {"vehicle_number": "MH 12 AB 4455", "vehicle_type": "20 ft SXL", "capacity_kg": 9000,
                                "temperature_class": "chiller", "body_type": "reefer"},
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
        self.assertEqual(hired_vehicle.temperature_class, "chiller")
        self.assertEqual(hired_vehicle.body_type, "reefer")
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


class TripCostApportionmentTests(BaseFleetOpsTest):
    """docs/ONE-TRIP-END-TO-END.md §3.2/§5 Phase 2: a trip's shared diesel and
    on-road cost, split fairly across the orders that actually rode on it,
    instead of the whole trip's cost being charged again to every order."""

    _order_counter = 0

    def _order(self, distance_km, weight_kg, **overrides):
        TripCostApportionmentTests._order_counter += 1
        defaults = dict(number=f"ORD-APRT-{TripCostApportionmentTests._order_counter}",
                        customer=self.customer, pickup=self.pickup, dropoff=self.dropoff,
                        distance_km=distance_km, weight_kg=weight_kg, freight_amount=10000,
                        total_amount=10000, status="created",
                        driver=self.driver, vehicle=self.vehicle)
        defaults.update(overrides)
        return Order.objects.create(**defaults)

    def test_apportion_splits_shared_cost_by_distance_proportionally(self):
        o1 = self._order(distance_km=100, weight_kg=1000)
        trip = o1.ensure_trip()
        o2 = self._order(distance_km=300, weight_kg=1000, trip=trip)
        FuelEntry.objects.create(vehicle=self.vehicle, trip=trip, volume_litres=100, rate_per_litre=100, amount=10000)

        from fleet.costing import apportion_trip_cost
        split = apportion_trip_cost(trip)
        self.assertEqual(split["basis"], "distance")
        # o1 is 100/400 of the distance, o2 is 300/400 - fuel splits the same way.
        self.assertEqual(split["orders"][o1.id]["fuel"], Decimal("2500.00"))
        self.assertEqual(split["orders"][o2.id]["fuel"], Decimal("7500.00"))

    def test_apportion_falls_back_to_weight_when_no_order_has_a_distance(self):
        o1 = self._order(distance_km=0, weight_kg=1000)
        trip = o1.ensure_trip()
        o2 = self._order(distance_km=0, weight_kg=3000, trip=trip)
        FuelEntry.objects.create(vehicle=self.vehicle, trip=trip, volume_litres=100, rate_per_litre=100, amount=8000)

        from fleet.costing import apportion_trip_cost
        split = apportion_trip_cost(trip)
        self.assertEqual(split["basis"], "weight")
        self.assertEqual(split["orders"][o1.id]["fuel"], Decimal("2000.00"))   # 1000/4000
        self.assertEqual(split["orders"][o2.id]["fuel"], Decimal("6000.00"))   # 3000/4000

    def test_apportion_falls_back_to_an_equal_split_when_neither_is_known(self):
        o1 = self._order(distance_km=0, weight_kg=0)
        trip = o1.ensure_trip()
        o2 = self._order(distance_km=0, weight_kg=0, trip=trip)
        FuelEntry.objects.create(vehicle=self.vehicle, trip=trip, volume_litres=100, rate_per_litre=100, amount=9000)

        from fleet.costing import apportion_trip_cost
        split = apportion_trip_cost(trip)
        self.assertEqual(split["basis"], "equal")
        self.assertEqual(split["orders"][o1.id]["fuel"], Decimal("4500.00"))
        self.assertEqual(split["orders"][o2.id]["fuel"], Decimal("4500.00"))

    def test_a_trip_keyed_expense_is_split_across_every_order_on_the_trip(self):
        """Before apportionment existed, an expense booked against only the
        trip (exactly what the trip-settlement upsert does) never appeared
        against any order's P&L at all."""
        o1 = self._order(distance_km=100, weight_kg=1000)
        trip = o1.ensure_trip()
        o2 = self._order(distance_km=100, weight_kg=1000, trip=trip)
        TripExpense.objects.create(trip=trip, vehicle=self.vehicle, category="toll", amount=1000)

        from fleet.costing import apportion_trip_cost
        split = apportion_trip_cost(trip)
        self.assertEqual(split["orders"][o1.id]["shared_expenses"], Decimal("500.00"))
        self.assertEqual(split["orders"][o2.id]["shared_expenses"], Decimal("500.00"))

    def test_an_order_attributed_expense_is_charged_to_that_order_alone(self):
        o1 = self._order(distance_km=100, weight_kg=1000)
        trip = o1.ensure_trip()
        o2 = self._order(distance_km=100, weight_kg=1000, trip=trip)
        TripExpense.objects.create(trip=trip, order=o1, vehicle=self.vehicle, category="unloading", amount=600)

        from fleet.costing import apportion_trip_cost
        split = apportion_trip_cost(trip)
        self.assertEqual(split["orders"][o1.id]["attributed_expenses"], Decimal("600.00"))
        self.assertEqual(split["orders"][o1.id]["shared_expenses"], Decimal("0.00"))
        self.assertEqual(split["orders"][o2.id]["attributed_expenses"], Decimal("0.00"))

    def test_reconciliation_apportioned_costs_sum_exactly_to_the_trips_total_cost(self):
        """The invariant this module exists to guarantee: split three ways (an
        amount that does not divide evenly), the parts must still sum to
        the whole to the paisa - this is what stops a consolidated trip's
        cost from quietly not adding up across its own orders."""
        o1 = self._order(distance_km=100, weight_kg=1000)
        trip = o1.ensure_trip()
        o2 = self._order(distance_km=137, weight_kg=1000, trip=trip)
        o3 = self._order(distance_km=253, weight_kg=1000, trip=trip)
        FuelEntry.objects.create(vehicle=self.vehicle, trip=trip, volume_litres=Decimal("33.33"),
                                 rate_per_litre=Decimal("97.77"), amount=Decimal("3258.65"))
        TripExpense.objects.create(trip=trip, vehicle=self.vehicle, category="toll", amount=Decimal("777.77"))
        TripExpense.objects.create(trip=trip, order=o2, vehicle=self.vehicle, category="unloading", amount=Decimal("311.11"))

        from fleet.costing import apportion_trip_cost
        split = apportion_trip_cost(trip)
        trip_total_cost = split["shared_total"] + sum(
            (row["attributed_expenses"] for row in split["orders"].values()), Decimal("0"))
        sum_of_orders = sum((row["total_cost"] for row in split["orders"].values()), Decimal("0"))
        self.assertEqual(sum_of_orders, trip_total_cost)
        for order in (o1, o2, o3):
            self.assertIn(order.id, split["orders"])

    def test_order_profitability_reports_the_apportioned_share_not_the_whole_trip(self):
        """The regression this whole module fixes: two orders on one
        consolidated trip must not each be charged the trip's entire fuel
        bill - docs/ONE-TRIP-END-TO-END.md §3.2 measured this at a 1.82x
        over-count with a legitimate trip margin reported as exactly zero."""
        o1 = self._order(distance_km=100, weight_kg=1000)
        trip = o1.ensure_trip()
        o2 = self._order(distance_km=100, weight_kg=1000, trip=trip)
        FuelEntry.objects.create(vehicle=self.vehicle, trip=trip, volume_litres=100, rate_per_litre=100, amount=10000)
        TripExpense.objects.create(trip=trip, vehicle=self.vehicle, category="toll", amount=1000)

        r1 = self.client.get(f"/api/v1/orders/{o1.id}/profitability/")
        r2 = self.client.get(f"/api/v1/orders/{o2.id}/profitability/")
        self.assertEqual(r1.data["fuel"], 5000.0)
        self.assertEqual(r2.data["fuel"], 5000.0)
        self.assertEqual(r1.data["trip_expenses"], 500.0)
        self.assertEqual(r1.data["cost_basis"], "distance")
        # Two 10,000 orders, 11,000 of real trip cost -> 9,000 of real margin,
        # not the zero profit the un-apportioned version reported on every order.
        self.assertEqual(r1.data["total_cost"] + r2.data["total_cost"], 11000.0)
        self.assertGreater(r1.data["profit"] + r2.data["profit"], 0)

    def test_order_with_no_trip_falls_back_to_its_own_directly_booked_expenses(self):
        order = self._order(distance_km=100, weight_kg=1000, driver=None, vehicle=None)
        TripExpense.objects.create(order=order, vehicle=self.vehicle, category="toll", amount=400)
        response = self.client.get(f"/api/v1/orders/{order.id}/profitability/")
        self.assertEqual(response.data["fuel"], 0.0)
        self.assertEqual(response.data["trip_expenses"], 400.0)
        self.assertEqual(response.data["cost_basis"], "order_only")

    def test_order_profitability_excludes_gst_from_revenue(self):
        order = self._order(distance_km=100, weight_kg=1000, freight_amount=10000,
                            tax_amount=500, total_amount=10500)
        response = self.client.get(f"/api/v1/orders/{order.id}/profitability/")
        self.assertEqual(response.data["revenue"], 10000.0)

    def test_tripexpense_save_backfills_trip_from_the_orders_trip(self):
        """An expense written with only `order` set must not go missing from
        the trip's own totals (`Trip.settlement_summary` aggregates
        `self.expenses`, i.e. rows with `trip` set)."""
        o1 = self._order(distance_km=100, weight_kg=1000)
        trip = o1.ensure_trip()
        expense = TripExpense.objects.create(order=o1, vehicle=self.vehicle, category="toll", amount=200)
        self.assertEqual(expense.trip_id, trip.id)

    def test_a_vehicle_only_expense_with_no_trip_or_order_is_still_legal(self):
        """A cost that never belonged to any one trip - an RTO fine, a permit -
        stays valid with neither FK set; fleet.billing.running_cost reads
        these by vehicle alone to learn a fleet's real cost per km."""
        expense = TripExpense.objects.create(vehicle=self.vehicle, category="fine", amount=500)
        self.assertIsNone(expense.trip_id)
        self.assertIsNone(expense.order_id)


class LorryReceiptGenerationTests(BaseFleetOpsTest):
    """docs/ONE-TRIP-END-TO-END.md §3.1/§5 Phase 1: the LR generated from the
    order it documents, instead of `Order.lorry_receipt` staying permanently
    null while an operator re-types the same details into a separate form."""

    def _order(self, **overrides):
        defaults = dict(number="ORD-LR-1", customer=self.customer, pickup=self.pickup, dropoff=self.dropoff,
                        payload_description="Packaged food cartons", weight_kg=12400, packages=480,
                        eway_bill_number="EWB1234567890", freight_amount=9700, status="created")
        defaults.update(overrides)
        return Order.objects.create(**defaults)

    def test_generate_lr_creates_one_from_the_order_and_links_it_back(self):
        order = self._order()
        response = self.client.post(f"/api/v1/orders/{order.id}/generate-lr/")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["created"])
        lr = response.data["lorry_receipt"]
        self.assertEqual(lr["material"], "Packaged food cartons")
        self.assertEqual(float(lr["weight_kg"]), 12400.0)
        self.assertEqual(lr["packages"], 480)
        self.assertEqual(lr["eway_bill_number"], "EWB1234567890")
        self.assertEqual(float(lr["freight_amount"]), 9700.0)
        self.assertEqual(lr["origin"], "Bhiwandi")
        self.assertEqual(lr["destination"], "Chakan")
        order.refresh_from_db()
        self.assertEqual(order.lorry_receipt_id, lr["id"])

    def test_generate_lr_accepts_a_manual_number(self):
        order = self._order()
        response = self.client.post(
            f"/api/v1/orders/{order.id}/generate-lr/", {"number": "  LR-MANUAL-0042  "}, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["lorry_receipt"]["number"], "LR-MANUAL-0042")
        order.refresh_from_db()
        self.assertEqual(order.lorry_receipt.number, "LR-MANUAL-0042")

    def test_generate_lr_rejects_a_duplicate_manual_number(self):
        existing_order = self._order(number="ORD-LR-EXISTING")
        self.client.post(
            f"/api/v1/orders/{existing_order.id}/generate-lr/", {"number": "LR-MANUAL-0042"}, format="json")
        order = self._order(number="ORD-LR-NEW")

        response = self.client.post(
            f"/api/v1/orders/{order.id}/generate-lr/", {"number": "lr-manual-0042"}, format="json")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("already exists", str(response.data).lower())
        order.refresh_from_db()
        self.assertIsNone(order.lorry_receipt_id)

    def test_consignor_and_consignee_fall_back_to_customer_and_dropoff_name(self):
        """Neither place has a contact name in the fixture, so the LR falls
        back to the order's customer (who is sending) and the dropoff place's
        own name (who is receiving)."""
        order = self._order()
        from fleet.lr import build_lr_from_order
        lr, _ = build_lr_from_order(order)
        self.assertEqual(lr.consignor, self.customer.name)
        self.assertEqual(lr.consignee, self.dropoff.name)

    def test_a_places_own_contact_name_wins_over_the_fallback(self):
        self.pickup.contact_name = "Bhiwandi Gate Security"; self.pickup.save()
        self.dropoff.contact_name = "Chakan Store Manager"; self.dropoff.save()
        order = self._order()
        from fleet.lr import build_lr_from_order
        lr, _ = build_lr_from_order(order)
        self.assertEqual(lr.consignor, "Bhiwandi Gate Security")
        self.assertEqual(lr.consignee, "Chakan Store Manager")

    def test_generate_lr_is_idempotent(self):
        order = self._order()
        first = self.client.post(f"/api/v1/orders/{order.id}/generate-lr/")
        second = self.client.post(f"/api/v1/orders/{order.id}/generate-lr/")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.data["created"])
        self.assertEqual(first.data["lorry_receipt"]["id"], second.data["lorry_receipt"]["id"])
        self.assertEqual(LorryReceipt.objects.filter(number=first.data["lorry_receipt"]["number"]).count(), 1)

    def test_generating_the_lr_adds_it_to_the_orders_trip(self):
        order = self._order(driver=self.driver, vehicle=self.vehicle)
        trip = order.ensure_trip()
        response = self.client.post(f"/api/v1/orders/{order.id}/generate-lr/")
        lr_id = response.data["lorry_receipt"]["id"]
        self.assertIn(lr_id, trip.lorry_receipts.values_list("id", flat=True))

    def test_dispatching_a_trip_auto_issues_an_lr_for_every_order_missing_one(self):
        o1 = self._order(number="ORD-LR-D1", driver=self.driver, vehicle=self.vehicle)
        trip = o1.ensure_trip()
        o2 = self._order(number="ORD-LR-D2", driver=self.driver, vehicle=self.vehicle, trip=trip)
        # o3 already has one - dispatch must not touch it or issue a second.
        o3 = self._order(number="ORD-LR-D3", driver=self.driver, vehicle=self.vehicle, trip=trip)
        from fleet.lr import build_lr_from_order
        existing_lr, _ = build_lr_from_order(o3)

        response = self.client.post(f"/api/v1/trips/{trip.id}/dispatch/")
        self.assertEqual(response.status_code, 200, response.data)
        for order in (o1, o2, o3):
            order.refresh_from_db()
            self.assertIsNotNone(order.lorry_receipt_id)
        self.assertEqual(o3.lorry_receipt_id, existing_lr.id)
        self.assertEqual(LorryReceipt.objects.filter(pk__in=[o1.lorry_receipt_id, o2.lorry_receipt_id]).count(), 2)
        # dispatch also advances every LR now linked to the trip to "dispatched".
        for order in (o1, o2, o3):
            self.assertEqual(order.lorry_receipt.status, "dispatched")

    def test_lr_pdf_downloads_for_a_generated_receipt(self):
        order = self._order()
        response = self.client.post(f"/api/v1/orders/{order.id}/generate-lr/")
        lr_id = response.data["lorry_receipt"]["id"]
        pdf = self.client.get(f"/api/v1/lorry-receipts/{lr_id}/pdf/")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))
        self.assertGreater(len(pdf.content), 3000)

    def test_lr_pdf_uses_the_landscape_way_bill_format(self):
        order = self._order(
            vehicle=self.vehicle,
            declared_value=250000,
            volume_cbm=Decimal("18.50"),
            other_charges=500,
            tax_amount=1836,
            total_amount=12036,
            payment_mode="tbb",
            special_instructions="Keep dry and call before delivery.",
        )
        response = self.client.post(f"/api/v1/orders/{order.id}/generate-lr/")
        lr = LorryReceipt.objects.get(pk=response.data["lorry_receipt"]["id"])

        from fleet.lr_pdf import _amount_words, render_lr_pdf

        pdf = render_lr_pdf(lr)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        # ReportLab records the A4 landscape width in the page MediaBox.
        self.assertIn(b"841.8898", pdf)
        self.assertEqual(_amount_words(Decimal("12036.10")),
                         "Rupees Twelve Thousand Thirty Six and Paise Ten Only")


class LorryReceiptFreightSyncTests(BaseFleetOpsTest):
    """An LR generated before its order was priced freezes at the wrong
    freight - usually zero - forever, because build_lr_from_order snapshots
    it once and nothing revisits it. fleet.lr.sync_lorry_receipt_freight and
    POST /lorry-receipts/{id}/sync-freight/ are the explicit fix."""

    def _order(self, **overrides):
        defaults = dict(number="ORD-LRS-1", customer=self.customer, pickup=self.pickup, dropoff=self.dropoff,
                        weight_kg=1000, distance_km=100, status="created")
        defaults.update(overrides)
        return Order.objects.create(**defaults)

    def test_an_lr_generated_before_pricing_is_stuck_at_zero_until_synced(self):
        from fleet.lr import build_lr_from_order, sync_lorry_receipt_freight
        order = self._order()   # no service_rate yet - freight_amount is 0
        lr, _ = build_lr_from_order(order)
        self.assertEqual(lr.freight_amount, Decimal("0.00"))

        order.service_rate = self.rate
        order.price_from_rate_card()
        order.refresh_from_db()
        self.assertGreater(order.freight_amount, 0)

        lr.refresh_from_db()
        self.assertEqual(lr.freight_amount, Decimal("0.00"), "the LR must still be stuck - nothing has synced it yet")

        lr, before, after = sync_lorry_receipt_freight(lr)
        self.assertEqual(before, Decimal("0.00"))
        self.assertEqual(after, order.freight_amount)
        self.assertEqual(lr.freight_amount, order.freight_amount)

    def test_sync_freight_endpoint_updates_the_lr(self):
        order = self._order(service_rate=self.rate)
        order.price_from_rate_card()
        order.refresh_from_db()
        from fleet.lr import build_lr_from_order
        lr, _ = build_lr_from_order(order)
        LorryReceipt.objects.filter(pk=lr.pk).update(freight_amount=0)   # simulate a stale snapshot

        response = self.client.post(f"/api/v1/lorry-receipts/{lr.pk}/sync-freight/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["changed"])
        self.assertEqual(response.data["after"], float(order.freight_amount))
        lr.refresh_from_db()
        self.assertEqual(lr.freight_amount, order.freight_amount)

    def test_sync_freight_rejects_a_standalone_lr_with_no_order(self):
        lr = LorryReceipt.objects.create(number="LR-STANDALONE-1", customer=self.customer,
                                         consignor="X", consignee="Y", origin="A", destination="B",
                                         material="General cargo", weight_kg=1000)
        response = self.client.post(f"/api/v1/lorry-receipts/{lr.pk}/sync-freight/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("no order linked", str(response.data).lower())

    def test_recalculating_a_trips_freight_also_syncs_its_lorry_receipts(self):
        """The scenario this exists for: a three-drop milk run whose LRs were
        generated before the trip's freight was correctly apportioned."""
        from fleet.lr import build_lr_from_order
        o1 = self._order(number="ORD-LRS-2", service_rate=self.rate, driver=self.driver, vehicle=self.vehicle,
                         status="completed", pod_required=False)
        trip = o1.ensure_trip()
        o2 = self._order(number="ORD-LRS-3", service_rate=self.rate, trip=trip, driver=self.driver,
                         vehicle=self.vehicle, status="completed", pod_required=False)
        lr1, _ = build_lr_from_order(o1)   # generated while freight_amount was still 0
        lr2, _ = build_lr_from_order(o2)
        self.assertEqual(lr1.freight_amount, Decimal("0.00"))
        self.assertEqual(lr2.freight_amount, Decimal("0.00"))

        response = self.client.post(f"/api/v1/trips/{trip.id}/recalculate-freight/",
                                    {"preview": False}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(set(response.data["lorry_receipts_synced"]), {lr1.number, lr2.number})

        o1.refresh_from_db(); o2.refresh_from_db(); lr1.refresh_from_db(); lr2.refresh_from_db()
        self.assertEqual(lr1.freight_amount, o1.freight_amount)
        self.assertEqual(lr2.freight_amount, o2.freight_amount)
        self.assertGreater(lr1.freight_amount, 0)


class TripCreationFromOrdersTests(BaseFleetOpsTest):
    """The Orders screen lets an operator pick several orders and book a trip
    for them in one call, instead of creating the trip and then linking each
    order in turn via add-order."""

    def _order(self, **overrides):
        defaults = dict(number="ORD-CFO-1", customer=self.customer, pickup=self.pickup, dropoff=self.dropoff,
                        payload_description="Packaged food cartons", weight_kg=12400, packages=480,
                        freight_amount=9700, status="created")
        defaults.update(overrides)
        return Order.objects.create(**defaults)

    def test_creates_a_trip_and_links_every_selected_order(self):
        o1 = self._order(number="ORD-CFO-1")
        o2 = self._order(number="ORD-CFO-2")

        response = self.client.post("/api/v1/trips/create-from-orders/", {
            "orders": [o1.id, o2.id], "vehicle": self.vehicle.id, "driver": self.driver.id,
        }, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        trip_id = response.data["id"]
        o1.refresh_from_db(); o2.refresh_from_db()
        self.assertEqual(o1.trip_id, trip_id)
        self.assertEqual(o2.trip_id, trip_id)
        self.assertEqual(o1.status, "assigned")
        self.assertEqual(o2.status, "assigned")
        self.assertEqual(o1.vehicle_id, self.vehicle.id)
        self.assertEqual(o2.driver_id, self.driver.id)
        self.assertIsNotNone(o1.scheduled_at)
        self.assertEqual(o1.activities.first().code, "ORDER_TRIP_ASSIGNED")
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.status, "allocated")
        self.assertEqual(self.vehicle.current_trip_id, trip_id)
        self.assertEqual(response.data["origin"], "Bhiwandi")
        self.assertEqual(response.data["destination"], "Chakan")

    def test_rejects_an_order_that_has_already_finished_its_workflow(self):
        order = self._order(number="ORD-CFO-DONE", status="completed")

        response = self.client.post("/api/v1/trips/create-from-orders/", {
            "orders": [order.id], "vehicle": self.vehicle.id, "driver": self.driver.id,
        }, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot be added", str(response.data))

    def test_dispatching_trip_updates_every_linked_order_and_generates_lr(self):
        o1 = self._order(number="ORD-CFO-DISPATCH-1")
        o2 = self._order(number="ORD-CFO-DISPATCH-2")
        created = self.client.post("/api/v1/trips/create-from-orders/", {
            "orders": [o1.id, o2.id], "vehicle": self.vehicle.id, "driver": self.driver.id,
        }, format="json")

        response = self.client.post(f"/api/v1/trips/{created.data['id']}/dispatch/")

        self.assertEqual(response.status_code, 200, response.data)
        o1.refresh_from_db(); o2.refresh_from_db()
        self.assertEqual(o1.status, "dispatched")
        self.assertEqual(o2.status, "dispatched")
        self.assertIsNotNone(o1.dispatched_at)
        self.assertIsNotNone(o2.dispatched_at)
        self.assertIsNotNone(o1.lorry_receipt_id)
        self.assertIsNotNone(o2.lorry_receipt_id)

    def test_multi_order_trip_stays_open_until_last_order_is_completed(self):
        o1 = self._order(number="ORD-CFO-CLOSE-1", pod_required=False)
        o2 = self._order(number="ORD-CFO-CLOSE-2", pod_required=False)
        created = self.client.post("/api/v1/trips/create-from-orders/", {
            "orders": [o1.id, o2.id], "vehicle": self.vehicle.id, "driver": self.driver.id,
        }, format="json")
        trip = Trip.objects.get(pk=created.data["id"])
        self.client.post(f"/api/v1/trips/{trip.id}/dispatch/")

        first = self.client.post(f"/api/v1/orders/{o1.id}/complete/")
        trip.refresh_from_db(); self.vehicle.refresh_from_db(); self.driver.refresh_from_db()
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(trip.status, "dispatched")
        self.assertEqual(self.vehicle.status, "running")
        self.assertEqual(self.driver.status, "on_trip")

        second = self.client.post(f"/api/v1/orders/{o2.id}/complete/")
        trip.refresh_from_db(); self.vehicle.refresh_from_db(); self.driver.refresh_from_db()
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(trip.status, "closed")
        self.assertEqual(self.vehicle.status, "available")
        self.assertEqual(self.driver.status, "available")

    def test_rejects_an_order_already_on_another_trip(self):
        o1 = self._order(number="ORD-CFO-3", driver=self.driver, vehicle=self.vehicle)
        o1.ensure_trip()

        response = self.client.post("/api/v1/trips/create-from-orders/", {
            "orders": [o1.id], "vehicle": self.vehicle.id, "driver": self.driver.id,
        }, format="json")

        self.assertEqual(response.status_code, 400)

    def test_requires_a_vehicle_and_a_driver(self):
        o1 = self._order(number="ORD-CFO-4")
        response = self.client.post("/api/v1/trips/create-from-orders/", {"orders": [o1.id]}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_requires_at_least_one_order(self):
        response = self.client.post("/api/v1/trips/create-from-orders/", {
            "orders": [], "vehicle": self.vehicle.id, "driver": self.driver.id,
        }, format="json")
        self.assertEqual(response.status_code, 400)


class TripCockpitTests(BaseFleetOpsTest):
    """docs/ONE-TRIP-END-TO-END.md §5 Phase 3: one trip's whole stack in one
    response - what used to take eight screens to piece together by hand."""

    def _order(self, **overrides):
        defaults = dict(number="ORD-CKPT-1", customer=self.customer, pickup=self.pickup, dropoff=self.dropoff,
                        distance_km=100, weight_kg=1000, total_amount=10000, freight_amount=10000,
                        status="created", driver=self.driver, vehicle=self.vehicle)
        defaults.update(overrides)
        return Order.objects.create(**defaults)

    def test_cockpit_lists_every_order_with_its_document_state(self):
        o1 = self._order(number="ORD-CKPT-A")
        trip = o1.ensure_trip()
        o2 = self._order(number="ORD-CKPT-B", trip=trip)

        from fleet.lr import build_lr_from_order
        build_lr_from_order(o1)   # o1 has an LR, o2 does not

        response = self.client.get(f"/api/v1/trips/{trip.id}/cockpit/")
        self.assertEqual(response.status_code, 200, response.data)
        rows = {row["number"]: row for row in response.data["orders"]}
        self.assertEqual(set(rows), {"ORD-CKPT-A", "ORD-CKPT-B"})
        self.assertIsNotNone(rows["ORD-CKPT-A"]["lorry_receipt"])
        self.assertIsNone(rows["ORD-CKPT-B"]["lorry_receipt"])
        self.assertIsNone(rows["ORD-CKPT-A"]["invoice"])
        self.assertFalse(rows["ORD-CKPT-A"]["pod"]["verified"])

    def test_cockpit_apportions_cost_consistently_with_order_profitability(self):
        o1 = self._order(number="ORD-CKPT-C", distance_km=100)
        trip = o1.ensure_trip()
        o2 = self._order(number="ORD-CKPT-D", distance_km=300, trip=trip)
        FuelEntry.objects.create(vehicle=self.vehicle, trip=trip, volume_litres=100, rate_per_litre=100, amount=10000)

        cockpit = self.client.get(f"/api/v1/trips/{trip.id}/cockpit/").data
        profitability = self.client.get(f"/api/v1/orders/{o1.id}/profitability/").data
        row = next(r for r in cockpit["orders"] if r["number"] == "ORD-CKPT-C")
        self.assertEqual(row["cost"]["fuel"], profitability["fuel"])
        self.assertEqual(cockpit["costs"]["basis"], "distance")
        self.assertEqual(cockpit["pnl"]["cost_basis"], "distance")

    def test_cockpit_headline_profit_includes_fuel_unlike_settlement_summary(self):
        """Trip.settlement_summary()'s trip_profit deliberately excludes fuel -
        it is a driver cash-advance reconciliation, since fuel may be paid on
        a company card rather than out of the driver's own advance - so it
        must never be the cockpit's own headline profit figure, or a trip's
        true cost reads as roughly its on-road expense alone."""
        order = self._order(total_amount=11550, freight_amount=11550)
        trip = order.ensure_trip()
        FuelEntry.objects.create(vehicle=self.vehicle, trip=trip, volume_litres=60, rate_per_litre=98, amount=5880)
        TripExpense.objects.create(trip=trip, vehicle=self.vehicle, category="toll", amount=450, status="approved")

        response = self.client.get(f"/api/v1/trips/{trip.id}/cockpit/")
        # settlement_summary()'s own trip_profit ignores the 5,880 of fuel entirely.
        self.assertEqual(response.data["pnl"]["total_exp"], 450.0)
        self.assertEqual(response.data["pnl"]["trip_profit"], 11550.0 - 450.0)
        # The cockpit's own headline figures must not repeat that omission.
        self.assertEqual(response.data["costs"]["total_cost"], 5880.0 + 450.0)
        self.assertEqual(response.data["profit"]["total"], 11550.0 - 5880.0 - 450.0)

    def test_cockpit_combines_only_freight_for_every_order_on_the_trip(self):
        first = self._order(number="ORD-CKPT-FREIGHT-A", freight_amount=10000,
                            tax_amount=500, total_amount=10500)
        trip = first.ensure_trip()
        self._order(number="ORD-CKPT-FREIGHT-B", trip=trip, freight_amount=7000,
                    tax_amount=350, total_amount=7350)

        response = self.client.get(f"/api/v1/trips/{trip.id}/cockpit/")
        self.assertEqual(response.data["revenue"]["total"], 17000.0)
        self.assertEqual(sum(row["revenue"] for row in response.data["orders"]), 17000.0)

    def test_cockpit_blockers_report_missing_lr_unverified_pod_and_unbilled_orders(self):
        order = self._order(pod_required=True)
        order.ensure_trip()
        trip = order.trip
        response = self.client.get(f"/api/v1/trips/{trip.id}/cockpit/")
        blockers = " ".join(response.data["blockers"])
        self.assertIn("lorry receipt", blockers)
        self.assertIn("POD", blockers)
        self.assertFalse(response.data["ready_to_close"])

    def test_cockpit_reports_unapproved_expenses_as_a_blocker(self):
        order = self._order()
        trip = order.ensure_trip()
        TripExpense.objects.create(trip=trip, vehicle=self.vehicle, category="toll", amount=500, status="pending")
        response = self.client.get(f"/api/v1/trips/{trip.id}/cockpit/")
        self.assertTrue(any("awaiting approval" in b for b in response.data["blockers"]))

    def test_cockpit_ready_to_close_once_every_blocker_is_cleared(self):
        order = self._order(pod_required=False)
        trip = order.ensure_trip()
        from fleet.lr import build_lr_from_order
        build_lr_from_order(order)
        order.status = "completed"; order.save(update_fields=["status"])
        Invoice.objects.create(number="INV-CKPT-1", customer=self.customer, trip=trip, order=order,
                               freight_amount=10000, tax_amount=0, due_date=timezone.localdate())
        trip.end_odometer_km = (trip.start_odometer_km or 0) + 100
        trip.save(update_fields=["end_odometer_km"])

        response = self.client.get(f"/api/v1/trips/{trip.id}/cockpit/")
        self.assertEqual(response.data["blockers"], [])
        self.assertTrue(response.data["ready_to_close"])

    def test_cockpit_documents_list_links_every_generated_document(self):
        order = self._order()
        trip = order.ensure_trip()
        from fleet.lr import build_lr_from_order
        build_lr_from_order(order)
        response = self.client.get(f"/api/v1/trips/{trip.id}/cockpit/")
        doc_types = {doc["type"] for doc in response.data["documents"]}
        self.assertIn("lorry_receipt", doc_types)

    def test_cockpit_surfaces_a_submitted_pod_awaiting_office_review(self):
        order = self._order()
        trip = order.ensure_trip()
        proof = ProofOfDelivery.objects.create(order=order, status="submitted", captured_at=timezone.now(),
                                               receiver_name="Store manager")
        response = self.client.get(f"/api/v1/trips/{trip.id}/cockpit/")
        row = next(r for r in response.data["orders"] if r["number"] == order.number)
        self.assertEqual(row["pod"]["pending_proof_id"], proof.id)
        self.assertTrue(row["pod"]["captured"])
        self.assertFalse(row["pod"]["verified"])


class ConsolidatedInvoicingTests(BaseFleetOpsTest):
    """docs/ONE-TRIP-END-TO-END.md §5 Phase 5: one invoice per customer for a
    trip's billable orders, instead of one per consignment even when several
    ride the same truck for the same customer."""

    def _order(self, number, **overrides):
        defaults = dict(number=number, customer=self.customer, pickup=self.pickup, dropoff=self.dropoff,
                        distance_km=100, weight_kg=1000, freight_amount=10000, tax_amount=500, total_amount=10500,
                        status="completed", driver=self.driver, vehicle=self.vehicle, pod_required=False)
        defaults.update(overrides)
        return Order.objects.create(**defaults)

    def test_consolidates_two_orders_for_the_same_customer_into_one_invoice(self):
        o1 = self._order("ORD-CINV-1")
        trip = o1.ensure_trip()
        o2 = self._order("ORD-CINV-2", trip=trip)

        response = self.client.post(f"/api/v1/trips/{trip.id}/consolidate-invoice/")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data["invoices"]), 1)
        invoice = response.data["invoices"][0]
        self.assertEqual(float(invoice["freight_amount"]), 20000.0)
        self.assertEqual(float(invoice["total_amount"]), 21000.0)
        self.assertEqual(len(invoice["line_items"]), 2)
        self.assertEqual({item["order_number"] for item in invoice["line_items"]}, {"ORD-CINV-1", "ORD-CINV-2"})
        o1.refresh_from_db(); o2.refresh_from_db()
        self.assertTrue(o1.consolidated_invoices.exists())
        self.assertTrue(o2.consolidated_invoices.exists())

    def test_groups_into_two_invoices_when_the_trip_carries_two_customers(self):
        other = Customer.objects.create(name="Other Co", gstin="27AAACT2727Q1ZX")
        o1 = self._order("ORD-CINV-3")
        trip = o1.ensure_trip()
        o2 = self._order("ORD-CINV-4", trip=trip, customer=other, total_amount=6000, freight_amount=5700, tax_amount=300)

        response = self.client.post(f"/api/v1/trips/{trip.id}/consolidate-invoice/")
        self.assertEqual(len(response.data["invoices"]), 2)
        customers = {inv["customer_name"] for inv in response.data["invoices"]}
        self.assertEqual(customers, {self.customer.name, "Other Co"})

    def test_skips_an_undelivered_order_and_reports_why(self):
        o1 = self._order("ORD-CINV-5")
        trip = o1.ensure_trip()
        o2 = self._order("ORD-CINV-6", trip=trip, status="dispatched", total_amount=0, freight_amount=0)

        response = self.client.post(f"/api/v1/trips/{trip.id}/consolidate-invoice/")
        self.assertEqual(len(response.data["invoices"]), 1)
        self.assertEqual(len(response.data["skipped"]), 1)
        self.assertEqual(response.data["skipped"][0]["order"], "ORD-CINV-6")
        self.assertEqual(response.data["skipped"][0]["reason"], "not yet delivered")

    def test_consolidate_invoice_is_idempotent(self):
        o1 = self._order("ORD-CINV-7")
        trip = o1.ensure_trip()
        first = self.client.post(f"/api/v1/trips/{trip.id}/consolidate-invoice/")
        second = self.client.post(f"/api/v1/trips/{trip.id}/consolidate-invoice/")
        self.assertEqual(len(first.data["invoices"]), 1)
        self.assertEqual(len(second.data["invoices"]), 0)   # nothing left unbilled
        self.assertEqual(Invoice.objects.filter(trip=trip).count(), 1)

    def test_cockpit_recognises_a_consolidated_invoice_against_each_order(self):
        """A consolidated invoice sets `Invoice.orders`, not the singular
        `Invoice.order` a single-order bill uses - the Trip Cockpit (and the
        order settlement sheet, and the billable-orders list) must recognise
        both, or an order billed this way looks perpetually unbilled."""
        o1 = self._order("ORD-CINV-COCKPIT-1")
        trip = o1.ensure_trip()
        o2 = self._order("ORD-CINV-COCKPIT-2", trip=trip)
        self.client.post(f"/api/v1/trips/{trip.id}/consolidate-invoice/")

        cockpit = self.client.get(f"/api/v1/trips/{trip.id}/cockpit/")
        self.assertFalse(any("invoiced" in b for b in cockpit.data["blockers"]))
        for row in cockpit.data["orders"]:
            self.assertIsNotNone(row["invoice"], f"{row['number']} should show its consolidated invoice")

        settlement = self.client.get(f"/api/v1/orders/{o1.id}/settlement/")
        self.assertIsNotNone(settlement.data["customer"])

        billable = self.client.get("/api/v1/orders/billable/")
        self.assertNotIn("ORD-CINV-COCKPIT-1", [row["number"] for row in billable.data["orders"]])

    def test_build_invoice_from_order_does_not_double_bill_a_consolidated_order(self):
        """The two billing paths must never both raise a bill for the same order."""
        o1 = self._order("ORD-CINV-8")
        trip = o1.ensure_trip()
        self.client.post(f"/api/v1/trips/{trip.id}/consolidate-invoice/")

        from fleet.billing import build_invoice_from_order
        invoice, created = build_invoice_from_order(o1)
        self.assertFalse(created)
        self.assertIn(o1, invoice.orders.all())

    def test_consolidated_invoice_posts_to_the_ledger(self):
        call_command("seed_accounting")
        o1 = self._order("ORD-CINV-9")
        trip = o1.ensure_trip()
        response = self.client.post(f"/api/v1/trips/{trip.id}/consolidate-invoice/")
        self.assertEqual(len(response.data["posted"]), 1)
        self.assertIn("journal_entry", response.data["posted"][0])
        self.assertEqual(JournalEntry.objects.filter(reference_type="invoice").count(), 1)

    def test_consolidated_invoice_pdf_renders_the_line_items(self):
        o1 = self._order("ORD-CINV-10")
        trip = o1.ensure_trip()
        o2 = self._order("ORD-CINV-11", trip=trip)
        response = self.client.post(f"/api/v1/trips/{trip.id}/consolidate-invoice/")
        invoice_id = response.data["invoices"][0]["id"]
        pdf = self.client.get(f"/api/v1/invoices/{invoice_id}/pdf/")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertGreater(len(pdf.content), 500)


class MultiPointFreightTests(BaseFleetOpsTest):
    """docs/MULTIPOINT-FREIGHT.md: a multi-point trip's rate-card freight was
    charged in full to every order riding it, rather than divided among them -
    a fixed per-trip card on three drops billed 3x. `fleet.freight` fixes it;
    these are its regression tests."""

    def _order(self, number, **overrides):
        defaults = dict(number=number, customer=self.customer, pickup=self.pickup, dropoff=self.dropoff,
                        distance_km=100, weight_kg=1000, service_rate=self.rate,
                        status="completed", driver=self.driver, vehicle=self.vehicle, pod_required=False)
        defaults.update(overrides)
        return Order.objects.create(**defaults)

    def test_a_shared_rate_cards_trip_level_charge_is_divided_not_replicated(self):
        """Three orders on one trip, one rate card with a base charge, a
        minimum and loading/unloading - the trip-level components must be
        charged once for the trip and divided, not once per drop."""
        o1 = self._order("ORD-MPF-1")
        trip = o1.ensure_trip()
        o2 = self._order("ORD-MPF-2", trip=trip)
        o3 = self._order("ORD-MPF-3", trip=trip)

        for order in (o1, o2, o3):
            order.price_from_rate_card()

        solo_breakdown = self.rate.quote(distance_km=100, weight_kg=1000)
        solo_freight = solo_breakdown["freight"] + solo_breakdown["fuel_surcharge"] + solo_breakdown["handling_charges"]

        o1.refresh_from_db(); o2.refresh_from_db(); o3.refresh_from_db()
        pooled_total = o1.freight_amount + o2.freight_amount + o3.freight_amount
        # Three equal-distance drops split a shared base/minimum/loading evenly, but
        # each still pays its own per-km leg, so the pooled total is somewhat more
        # than one order's solo freight - the point is it must be far below 3x it.
        self.assertLess(pooled_total, solo_freight * 3)
        self.assertGreater(pooled_total, solo_freight)
        # Each order still has its own per-km leg alongside its share of the
        # shared base/minimum/loading, so the source is "mixed", not "trip_share".
        for order in (o1, o2, o3):
            self.assertEqual(order.freight_source, "mixed")

    def test_an_order_with_a_different_rate_card_is_priced_on_its_own(self):
        """§3: an order with its own rate card is priced from it, not pooled
        with a sibling's card."""
        milk_run = self._order("ORD-MPF-SHARED")
        trip = milk_run.ensure_trip()
        solo_rate = ServiceRate.objects.create(name="Solo lane", code="RC-MPF-SOLO",
                                               rate_type="per_km", per_km_rate=40, gst_percent=5)
        solo = self._order("ORD-MPF-SOLO", trip=trip, service_rate=solo_rate, distance_km=50)

        milk_run.price_from_rate_card()
        solo.price_from_rate_card()
        solo.refresh_from_db()
        self.assertEqual(solo.freight_amount, Decimal("2000.00"))   # 50 * 40, untouched by the other card
        self.assertEqual(solo.freight_source, "rate_card")

    def test_a_solo_order_prices_identically_to_plain_quote(self):
        """Backward compatibility: an order alone on its trip must price
        exactly as `ServiceRate.quote()` always has."""
        order = self._order("ORD-MPF-SOLO-2")
        order.ensure_trip()
        direct = self.rate.quote(distance_km=100, weight_kg=1000)
        order.price_from_rate_card()
        self.assertEqual(order.freight_amount, money(direct["freight"] + direct["fuel_surcharge"] + direct["handling_charges"]))
        self.assertEqual(order.tax_amount, money(direct["gst_amount"]))
        self.assertEqual(order.total_amount, money(direct["total"]))
        self.assertEqual(order.freight_source, "rate_card")

    def test_settlement_freight_excludes_gst(self):
        order = self._order("ORD-MPF-GST")
        trip = order.ensure_trip()
        order.price_from_rate_card()
        order.refresh_from_db()
        summary = trip.settlement_summary()
        self.assertEqual(summary["freight"], float(order.freight_amount))
        self.assertLess(order.freight_amount, order.total_amount)

    def test_consolidated_invoice_does_not_triple_bill_a_three_drop_milk_run(self):
        o1 = self._order("ORD-MPF-INV-1")
        trip = o1.ensure_trip()
        o2 = self._order("ORD-MPF-INV-2", trip=trip)
        o3 = self._order("ORD-MPF-INV-3", trip=trip)

        response = self.client.post(f"/api/v1/trips/{trip.id}/consolidate-invoice/")
        self.assertEqual(response.status_code, 201, response.data)
        invoice = response.data["invoices"][0]
        solo_breakdown = self.rate.quote(distance_km=100, weight_kg=1000)
        solo_total = solo_breakdown["freight"] + solo_breakdown["fuel_surcharge"] + solo_breakdown["handling_charges"]
        self.assertLess(float(invoice["freight_amount"]), solo_total * 3)

    def test_recalculate_freight_previews_by_default_and_writes_nothing(self):
        o1 = self._order("ORD-MPF-RC-1")
        trip = o1.ensure_trip()
        o2 = self._order("ORD-MPF-RC-2", trip=trip)

        response = self.client.post(f"/api/v1/trips/{trip.id}/recalculate-freight/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["preview"])
        self.assertGreater(response.data["total_after"], 0)
        o1.refresh_from_db(); o2.refresh_from_db()
        self.assertEqual(o1.freight_amount, Decimal("0.00"))   # preview must not mutate
        self.assertEqual(o2.freight_amount, Decimal("0.00"))

    def test_recalculate_freight_applies_when_preview_is_false(self):
        o1 = self._order("ORD-MPF-RC-3")
        trip = o1.ensure_trip()
        o2 = self._order("ORD-MPF-RC-4", trip=trip)

        response = self.client.post(f"/api/v1/trips/{trip.id}/recalculate-freight/",
                                    {"preview": False}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["preview"])
        self.assertTrue(response.data["reconciliation"]["balanced"])
        o1.refresh_from_db(); o2.refresh_from_db()
        self.assertGreater(o1.freight_amount, 0)
        self.assertGreater(o2.freight_amount, 0)

    def test_recalculate_freight_is_idempotent(self):
        o1 = self._order("ORD-MPF-RC-5")
        trip = o1.ensure_trip()
        self._order("ORD-MPF-RC-6", trip=trip)

        first = self.client.post(f"/api/v1/trips/{trip.id}/recalculate-freight/", {"preview": False}, format="json")
        second = self.client.post(f"/api/v1/trips/{trip.id}/recalculate-freight/", {"preview": False}, format="json")
        self.assertEqual(first.data["total_after"], second.data["total_after"])
        self.assertTrue(all(row["delta"] == 0 for row in second.data["orders"]))

    def test_recalculate_freight_skips_an_invoiced_order_without_force(self):
        o1 = self._order("ORD-MPF-RC-7")
        trip = o1.ensure_trip()
        o2 = self._order("ORD-MPF-RC-8", trip=trip)
        self.client.post(f"/api/v1/trips/{trip.id}/recalculate-freight/", {"preview": False}, format="json")
        o1.refresh_from_db()
        from fleet.billing import build_invoice_from_order
        build_invoice_from_order(o1)

        response = self.client.post(f"/api/v1/trips/{trip.id}/recalculate-freight/",
                                    {"preview": False}, format="json")
        row = next(r for r in response.data["orders"] if r["number"] == "ORD-MPF-RC-7")
        self.assertEqual(row["blocked"], "already invoiced")

    def test_adding_an_order_to_a_trip_reprices_every_order_on_it(self):
        """Adding an order changes every other order's share (§4.4) - the
        sibling created first must not be left stale at its solo price."""
        o1 = self._order("ORD-MPF-ADD-1")
        trip = o1.ensure_trip()
        o1.price_from_rate_card()
        o1.refresh_from_db()
        solo_freight = o1.freight_amount
        self.assertGreater(solo_freight, 0)

        o2 = self._order("ORD-MPF-ADD-2", status="created", driver=None, vehicle=None)
        response = self.client.post(f"/api/v1/trips/{trip.id}/add-order/", {"order": o2.id}, format="json")
        self.assertEqual(response.status_code, 200, response.data)

        o1.refresh_from_db(); o2.refresh_from_db()
        self.assertNotEqual(o1.freight_amount, solo_freight, "the first order must be repriced too, not left stale")
        self.assertGreater(o2.freight_amount, 0)

    def test_reconcile_trip_freight_flags_a_stale_order(self):
        from fleet.freight import reconcile_trip_freight
        o1 = self._order("ORD-MPF-STALE-1")
        trip = o1.ensure_trip()
        o1.price_from_rate_card()
        self._order("ORD-MPF-STALE-2", trip=trip)   # joined after o1 was priced, never repriced

        reconciliation = reconcile_trip_freight(trip)
        self.assertFalse(reconciliation["balanced"])
        self.assertEqual(len(reconciliation["stale_orders"]), 2)

        self.client.post(f"/api/v1/trips/{trip.id}/recalculate-freight/", {"preview": False}, format="json")
        self.assertTrue(reconcile_trip_freight(trip)["balanced"])

    def test_cockpit_surfaces_the_recalculate_blocker(self):
        o1 = self._order("ORD-MPF-CKPT-1")
        trip = o1.ensure_trip()
        o1.price_from_rate_card()
        self._order("ORD-MPF-CKPT-2", trip=trip)

        cockpit = self.client.get(f"/api/v1/trips/{trip.id}/cockpit/")
        self.assertFalse(cockpit.data["freight_reconciliation"]["balanced"])
        self.assertTrue(any("recalculate freight" in b for b in cockpit.data["blockers"]))


class SalesReportTests(BaseFleetOpsTest):
    def test_report_contains_lr_delivery_vehicle_and_billing_details(self):
        self.customer.billing_party_name = "Tata Consumer Accounts"
        self.customer.save(update_fields=["billing_party_name"])
        self.vehicle.temperature_class = "chiller"
        self.vehicle.body_type = "reefer"
        self.vehicle.save(update_fields=["temperature_class", "body_type"])

        completed_at = timezone.now()
        expected_at = completed_at + timedelta(hours=2)
        lr = LorryReceipt.objects.create(
            number="LR-SALES-01", customer=self.customer, consignor="Bhiwandi Plant",
            consignee="Chakan Store", origin="Bhiwandi", destination="Chakan",
            material="Cartons", weight_kg=Decimal("12400.50"), packages=480,
            freight_amount=Decimal("28500.75"))
        trip = Trip.objects.create(
            number="TRP-SALES-01", vehicle=self.vehicle, driver=self.driver,
            origin="Bhiwandi", destination="Chakan", planned_departure=timezone.now())
        trip.lorry_receipts.add(lr)
        Order.objects.create(
            number="ORD-SALES-01", customer=self.customer, pickup=self.pickup, dropoff=self.dropoff,
            lorry_receipt=lr, trip=trip, vehicle=self.vehicle, driver=self.driver,
            packages=480, weight_kg=Decimal("12400.50"), freight_amount=Decimal("28500.75"),
            temperature_class="chiller", expected_delivery_at=expected_at,
            completed_at=completed_at, status="completed")

        response = self.client.get("/api/v1/reports/sales/")

        self.assertEqual(response.status_code, 200, response.data)
        row = next(item for item in response.data["sales"] if item["lr_no"] == lr.number)
        self.assertEqual(row["lr_date"], str(lr.created_at.date()))
        self.assertEqual(row["from"], "Bhiwandi")
        self.assertEqual(row["to"], "Chakan")
        self.assertEqual(row["actual_delivery_date"], str(completed_at.date()))
        self.assertEqual(row["expected_delivery_date"], str(expected_at.date()))
        self.assertEqual(row["no_of_boxes"], 480)
        self.assertEqual(row["weight_kg"], 12400.5)
        self.assertEqual(row["vehicle_no"], "MH 04 JU 9182")
        self.assertEqual(row["vehicle_type_and_capacity"], "32 ft MXL / 16,000 kg")
        self.assertEqual(row["freight"], 28500.75)
        self.assertEqual(row["dry_reefer"], "Reefer")
        self.assertEqual(row["consignor"], "Bhiwandi Plant")
        self.assertEqual(row["consignee"], "Chakan Store")
        self.assertEqual(row["billing_party_name"], "Tata Consumer Accounts")
        self.assertEqual(response.data["sales_summary"]["total_lrs"], 1)
        self.assertEqual(response.data["sales_summary"]["delivered"], 1)

    def test_standalone_lr_remains_in_report_with_blank_operational_links(self):
        lr = LorryReceipt.objects.create(
            number="LR-SALES-LEGACY", customer=self.customer, consignor="Legacy Sender",
            consignee="Legacy Receiver", origin="Mumbai", destination="Pune",
            material="General cargo", weight_kg=100, packages=2, freight_amount=5000)

        response = self.client.get("/api/v1/reports/sales/")

        row = next(item for item in response.data["sales"] if item["lr_no"] == lr.number)
        self.assertIsNone(row["actual_delivery_date"])
        self.assertIsNone(row["expected_delivery_date"])
        self.assertEqual(row["vehicle_no"], "")
        self.assertEqual(row["vehicle_type_and_capacity"], "")
        self.assertEqual(row["dry_reefer"], "Dry")
        self.assertEqual(row["billing_party_name"], self.customer.name)


class TripProfitabilityReportTests(BaseFleetOpsTest):
    """docs/ONE-TRIP-END-TO-END.md §5 Phase 5: trip-wise P&L across a date
    range, using the same apportioned cost as the Trip Cockpit."""

    def _order(self, number, **overrides):
        defaults = dict(number=number, customer=self.customer, pickup=self.pickup, dropoff=self.dropoff,
                        distance_km=100, weight_kg=1000, freight_amount=10000,
                        total_amount=10000, status="completed",
                        driver=self.driver, vehicle=self.vehicle)
        defaults.update(overrides)
        return Order.objects.create(**defaults)

    def test_report_returns_apportioned_revenue_cost_and_margin_per_trip(self):
        order = self._order("ORD-TPR-1", freight_amount=9500, tax_amount=500, total_amount=10000)
        trip = order.ensure_trip()
        FuelEntry.objects.create(vehicle=self.vehicle, trip=trip, volume_litres=50, rate_per_litre=100, amount=5000)

        response = self.client.get("/api/v1/reports/trip-profitability/")
        self.assertEqual(response.status_code, 200)
        row = next(r for r in response.data["trips"] if r["number"] == trip.number)
        self.assertEqual(row["revenue"], 9500.0)
        self.assertEqual(row["fuel"], 5000.0)
        self.assertEqual(row["cost"], 5000.0)
        self.assertEqual(row["margin"], 4500.0)
        self.assertEqual(row["order_count"], 1)

    def test_report_excludes_trips_with_no_orders(self):
        Trip.objects.create(number="TRP-TPR-EMPTY", vehicle=self.vehicle, driver=self.driver,
                            origin="Bhiwandi", destination="Chakan", planned_departure=timezone.now())
        response = self.client.get("/api/v1/reports/trip-profitability/")
        self.assertNotIn("TRP-TPR-EMPTY", [r["number"] for r in response.data["trips"]])

    def test_report_filters_by_vehicle(self):
        order = self._order("ORD-TPR-2")
        trip = order.ensure_trip()
        other_vehicle = Vehicle.objects.create(registration_number="MH 20 TP 0002", vehicle_type="20 ft SXL", capacity_kg=8000)

        matching = self.client.get(f"/api/v1/reports/trip-profitability/?vehicle={self.vehicle.id}")
        other = self.client.get(f"/api/v1/reports/trip-profitability/?vehicle={other_vehicle.id}")
        self.assertIn(trip.number, [r["number"] for r in matching.data["trips"]])
        self.assertNotIn(trip.number, [r["number"] for r in other.data["trips"]])

    def test_report_filters_by_customer(self):
        other = Customer.objects.create(name="Filter Co", gstin="27AAACT2727Q1XY")
        order = self._order("ORD-TPR-3")
        trip = order.ensure_trip()

        matching = self.client.get(f"/api/v1/reports/trip-profitability/?customer={self.customer.id}")
        other_result = self.client.get(f"/api/v1/reports/trip-profitability/?customer={other.id}")
        self.assertIn(trip.number, [r["number"] for r in matching.data["trips"]])
        self.assertNotIn(trip.number, [r["number"] for r in other_result.data["trips"]])

    def test_report_summary_totals_match_the_row_sums(self):
        o1 = self._order("ORD-TPR-4", freight_amount=8000, total_amount=8000)
        trip1 = o1.ensure_trip()
        o2 = self._order("ORD-TPR-5", freight_amount=6000, total_amount=6000)
        trip2 = o2.ensure_trip()

        response = self.client.get("/api/v1/reports/trip-profitability/")
        rows = [r for r in response.data["trips"] if r["number"] in (trip1.number, trip2.number)]
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(response.data["summary"]["total_revenue"],
                               sum(r["revenue"] for r in response.data["trips"]), places=2)


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

    def test_customer_kyc_keeps_billing_delivery_and_vendor_vehicle_terms(self):
        response = self.client.post("/api/v1/customers/", {
            "name": "Cold Chain Foods", "gstin": "27AACCC1234A1Z1",
            "billing_party_name": "Cold Chain Foods Accounts",
            "billing_contact_name": "Priya Shah", "billing_contact_phone": "+919876543210",
            "billing_contact_email": "accounts@coldchain.example",
            "billing_address": "Taloja MIDC, Navi Mumbai",
            "pod_preference": "both", "invoice_delivery_preference": "soft_copy",
            "customer_type": "reefer", "payment_terms_days": 45,
            "vendor_vehicle_advance_percent": "90.00",
            "vendor_vehicle_advance_trigger": "after_loading",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["billing_party_name"], "Cold Chain Foods Accounts")
        self.assertEqual(response.data["pod_preference"], "both")
        self.assertEqual(response.data["invoice_delivery_preference"], "soft_copy")
        self.assertEqual(response.data["customer_type"], "reefer")
        self.assertEqual(response.data["payment_terms_days"], 45)
        self.assertEqual(response.data["vendor_vehicle_advance_percent"], "90.00")
        self.assertEqual(response.data["vendor_vehicle_advance_trigger"], "after_loading")

    def test_customer_kyc_rejects_unapproved_payment_terms(self):
        response = self.client.post("/api/v1/customers/", {
            "name": "Invalid Terms Customer", "gstin": "27AACCC1234A1Z2",
            "payment_terms_days": 60,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("payment_terms_days", response.data)

    def test_customer_kyc_rejects_vendor_advance_over_one_hundred_percent(self):
        response = self.client.post("/api/v1/customers/", {
            "name": "Invalid Advance Customer", "gstin": "27AACCC1234A1Z3",
            "vendor_vehicle_advance_percent": "101.00",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("vendor_vehicle_advance_percent", response.data)


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

    def test_saving_the_trip_settlement_syncs_the_driver_settlement_ledger(self):
        """The whole point of the trip sheet is that accounting shouldn't have to
        re-type these same numbers into the driver settlement module by hand."""
        trip = self.create_trip()
        response = self.client.post(f"/api/v1/trips/{trip.id}/settlement/", {
            "diesel_given": "39000", "expenses": {"diesel": "26400", "halting": "2500"},
        }, format="json")
        settlement = response.data["settlement"]
        self.assertEqual(settlement["trip"], trip.id)
        self.assertEqual(settlement["driver"], self.driver.id)
        self.assertEqual(Decimal(settlement["advance_amount"]), Decimal("39000.00"))
        self.assertEqual(Decimal(settlement["approved_expenses"]), Decimal("28900.00"))
        self.assertEqual(Decimal(settlement["net_payable"]), Decimal("-10100.00"))
        self.assertEqual(settlement["status"], "pending")
        self.assertEqual(Settlement.objects.filter(trip=trip, driver=self.driver).count(), 1)

        get_response = self.client.get(f"/api/v1/trips/{trip.id}/settlement/")
        self.assertEqual(get_response.data["settlement"]["id"], settlement["id"])

        # Resubmitting with different figures keeps updating the same row while it's pending.
        # "halting" is untouched by this payload, so its 2,500 line survives alongside the new diesel figure.
        response = self.client.post(f"/api/v1/trips/{trip.id}/settlement/",
                                    {"diesel_given": "39000", "expenses": {"diesel": "30000"}}, format="json")
        self.assertEqual(response.data["settlement"]["id"], settlement["id"])
        self.assertEqual(Decimal(response.data["settlement"]["approved_expenses"]), Decimal("32500.00"))
        self.assertEqual(Settlement.objects.filter(trip=trip, driver=self.driver).count(), 1)

    def test_an_approved_settlement_is_not_silently_overwritten_by_a_later_trip_sheet_edit(self):
        trip = self.create_trip()
        first = self.client.post(f"/api/v1/trips/{trip.id}/settlement/",
                                 {"diesel_given": "39000", "expenses": {"diesel": "26400"}}, format="json")
        settlement_id = first.data["settlement"]["id"]
        settlement = Settlement.objects.get(pk=settlement_id)
        settlement.status = "approved"; settlement.save(update_fields=["status"])

        response = self.client.post(f"/api/v1/trips/{trip.id}/settlement/",
                                    {"diesel_given": "39000", "expenses": {"diesel": "50000"}}, format="json")
        self.assertEqual(response.data["settlement"]["status"], "approved")
        self.assertEqual(Decimal(response.data["settlement"]["approved_expenses"]), Decimal("26400.00"))
        self.assertEqual(Settlement.objects.filter(trip=trip, driver=self.driver).count(), 1)


class SettlementBackfillMigrationTests(BaseFleetOpsTest):
    """Trips settled before the settlement-save -> Settlement sync existed (migration
    0021) need the same catch-up, run here against the live models the same way the
    migration's RunPython step does."""

    def run_backfill(self):
        import importlib
        from django.apps import apps
        migration = importlib.import_module("fleet.migrations.0021_backfill_settlement_from_existing_trips")
        migration.backfill_settlements(apps, None)

    def create_trip(self, number="TRP-BACKFILL"):
        response = self.client.post("/api/v1/trips/", {
            "number": number, "vehicle": self.vehicle.id, "driver": self.driver.id,
            "origin": "Surat", "destination": "Delhi-Noida", "planned_departure": "2026-07-05T08:00:00Z"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return Trip.objects.get(pk=response.data["id"])

    def test_backfill_creates_a_settlement_for_a_trip_with_expenses_but_no_settlement_row(self):
        trip = self.create_trip()
        trip.advance_amount = Decimal("39000.00"); trip.save(update_fields=["advance_amount"])
        TripExpense.objects.create(trip=trip, driver=self.driver, vehicle=self.vehicle,
                                   category="diesel", amount=Decimal("26400.00"), status="approved")
        TripExpense.objects.create(trip=trip, driver=self.driver, vehicle=self.vehicle,
                                   category="halting", amount=Decimal("2500.00"), status="approved")

        self.run_backfill()

        settlement = Settlement.objects.get(trip=trip, driver=self.driver)
        self.assertEqual(settlement.advance_amount, Decimal("39000.00"))
        self.assertEqual(settlement.approved_expenses, Decimal("28900.00"))
        self.assertEqual(settlement.net_payable, Decimal("-10100.00"))
        self.assertEqual(settlement.status, "pending")

    def test_backfill_skips_a_trip_with_neither_expenses_nor_an_advance(self):
        self.create_trip()
        self.run_backfill()
        self.assertEqual(Settlement.objects.count(), 0)

    def test_backfill_never_touches_a_trip_that_already_has_a_settlement(self):
        trip = self.create_trip()
        TripExpense.objects.create(trip=trip, driver=self.driver, vehicle=self.vehicle,
                                   category="diesel", amount=Decimal("5000.00"), status="approved")
        Settlement.objects.create(trip=trip, driver=self.driver, advance_amount=Decimal("1000.00"),
                                  approved_expenses=Decimal("500.00"), net_payable=Decimal("-500.00"),
                                  status="approved")

        self.run_backfill()

        self.assertEqual(Settlement.objects.filter(trip=trip, driver=self.driver).count(), 1)
        settlement = Settlement.objects.get(trip=trip, driver=self.driver)
        self.assertEqual(settlement.approved_expenses, Decimal("500.00"))   # untouched, not recomputed to 5000
        self.assertEqual(settlement.status, "approved")

    def test_settlement_rejects_an_unknown_expense_category(self):
        trip = self.create_trip()
        response = self.client.post(f"/api/v1/trips/{trip.id}/settlement/",
                                    {"expenses": {"not_a_real_category": "100"}}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_get_settlement_reflects_a_linked_orders_freight(self):
        """A trip created from an order prices its settlement off that order,
        not a manually typed freight figure. freight_amount, not total_amount:
        settlement freight excludes GST - see docs/MULTIPOINT-FREIGHT.md §2.5."""
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
        self.assertEqual(response.data["summary"]["freight"], float(order.freight_amount))

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

    def test_a_model_name_is_not_mistaken_for_a_plate(self):
        self.assertEqual(geotrackers.registration_from("TATA 1109"), "")
        self.assertEqual(geotrackers.registration_from("TATA 1109 - MH43CK1211"), "MH43CK1211")

    def test_an_asset_with_no_plate_at_all_keeps_its_name(self):
        ping = geotrackers.parse([{"assetName": "Yard genset", "lat": 19.2, "lng": 73.0}])[0]
        self.assertEqual(ping["reg"], "Yard genset")

    def test_a_clean_plate_is_not_duplicated_into_the_label(self):
        ping = geotrackers.parse([{"vehicleNo": "MH04JU9182", "lat": 19.2, "lng": 73.0}])[0]
        self.assertEqual(ping["reg"], "MH04JU9182")
        self.assertEqual(ping["label"], "")


# One real record captured from the vendor, kept verbatim. Everything the
# console shows is derived from this shape, so it is the fixture that matters.
GEOTRACKERS_LIVE_RECORD = {
    "id": None,
    "vehicle": {
        "id": "a717c5f1-86ea-4f46-a077-bb5a44314dbd",
        "registrationNumber": "10 FT 1 TON BOLR MH14LX8206",
        "make": "Reefer", "model": "2820", "type": "Reefer",
        "fmId": "3ea104f150768a07fd55453b14585368fd7dd975d8a05a9423930b45f442becf",
        "createdBy": "alpha", "createdAt": 1774092500482, "virtualName": None,
    },
    "loadStatus": None,
    "driver": None,
    "location": {
        "id": "257381842439", "vtuId": 47598, "timestamp": 1786535659000,
        "address": "2.3 kms from Government Hospital, Sangamner Kh, Sangamner Bypass, "
                   "Sangamner, Ahilyanagar District, MH",
        "latitude": 19.54, "longitude": 74.2043, "speed": 30, "speedUnit": "kmph",
        "direction": 202, "distance": None, "exceptionBM": 524336, "exceptionBM2": 0,
        "state": "Moving for 49 mins ", "status": "AC On, Key On, Motion",
        "sysTime": 0, "lateData": 0,
    },
    "analogs": [
        {"type": "TEMPERATURE", "name": "Temperature", "value": -22.8, "unit": "°C",
         "disOrShortValue": None, "disOrShort": False},
        {"type": "BATTERY", "name": "VBatt", "value": 14.292, "unit": "V",
         "disOrShortValue": None, "disOrShort": False},
    ],
    "digitals": [
        {"type": "AC", "name": "AC On", "bit": 32, "state": True},
        {"type": "KEY", "name": "Key On", "bit": 16, "state": True},
        {"type": "CUSTOM", "name": "Motion", "bit": 524288, "state": True},
        {"type": "CUSTOM", "name": None, "bit": 1048576, "state": False},
    ],
    "trips": None,
    "fuelTankCapacity": 0,
}


class GeotrackersLiveRecordTests(TestCase):
    """Pinned against the real vendor record rather than an invented one."""

    def setUp(self):
        self.ping = geotrackers.parse([GEOTRACKERS_LIVE_RECORD])[0]

    def test_the_plate_is_extracted_from_the_descriptive_registration(self):
        self.assertEqual(self.ping["reg"], "MH14LX8206")
        self.assertEqual(self.ping["label"], "10 FT 1 TON BOLR MH14LX8206")

    def test_coordinates_come_off_the_nested_location_object(self):
        self.assertAlmostEqual(self.ping["lat"], 19.54)
        self.assertAlmostEqual(self.ping["lng"], 74.2043)
        self.assertTrue(self.ping["in_india"])
        self.assertFalse(self.ping["coords_swapped"])

    def test_epoch_milliseconds_become_a_readable_timestamp(self):
        # 1786535659000 ms, not the integer printed straight at the operator.
        self.assertTrue(self.ping["gps_time"].startswith("2026-"))
        self.assertIn("T", self.ping["gps_time"])

    def test_reefer_temperature_and_battery_are_lifted_off_the_analogs_array(self):
        self.assertAlmostEqual(self.ping["temperature_c"], -22.8)
        self.assertAlmostEqual(self.ping["battery_v"], 14.292)

    def test_ignition_and_reefer_ac_come_off_the_digitals_array(self):
        self.assertTrue(self.ping["ignition"])   # KEY
        self.assertTrue(self.ping["ac_on"])      # AC

    def test_movement_is_read_from_the_state_wording(self):
        self.assertEqual(self.ping["gps_status"], "moving")
        self.assertEqual(self.ping["state_text"], "Moving for 49 mins")
        self.assertEqual(self.ping["speed_kph"], 30.0)
        self.assertEqual(self.ping["heading"], 202.0)

    def test_a_stopped_reading_is_not_reported_as_moving(self):
        record = _json.loads(_json.dumps(GEOTRACKERS_LIVE_RECORD))
        record["location"].update({"state": "Stopped for 3 hrs ", "status": "AC Off, Key Off", "speed": 0})
        record["digitals"] = [{"type": "KEY", "name": "Key Off", "bit": 16, "state": False}]
        ping = geotrackers.parse([record])[0]
        self.assertEqual(ping["gps_status"], "parked")
        self.assertFalse(ping["ignition"])

    def test_the_telemetry_arrays_are_not_mistaken_for_the_vehicle_list(self):
        # `digitals` and `analogs` are lists of dicts too; the scorer has to
        # prefer the list whose entries carry coordinates.
        rows = geotrackers.extract_rows({"data": [GEOTRACKERS_LIVE_RECORD]})
        self.assertEqual(len(rows), 1)
        self.assertIn("vehicle", rows[0])


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



class GeotrackersCredentialTests(TestCase):
    """The vendor rotates credentials on their own schedule; that must not need
    a code change and a redeploy."""

    def test_a_username_and_password_are_encoded_into_the_header(self):
        with self.settings(GEOTRACKERS_USERNAME="newuser", GEOTRACKERS_PASSWORD="newpass",
                           GEOTRACKERS_BASIC_AUTH=""):
            self.assertEqual(geotrackers.basic_auth(),
                             base64.b64encode(b"newuser:newpass").decode())

    def test_a_pre_encoded_value_is_used_verbatim(self):
        with self.settings(GEOTRACKERS_BASIC_AUTH="QUJDOmRlZg==",
                           GEOTRACKERS_USERNAME="ignored", GEOTRACKERS_PASSWORD="ignored"):
            self.assertEqual(geotrackers.basic_auth(), "QUJDOmRlZg==")

    def test_an_unconfigured_deployment_keeps_the_shipped_credential(self):
        with self.settings(GEOTRACKERS_BASIC_AUTH="", GEOTRACKERS_USERNAME="", GEOTRACKERS_PASSWORD=""):
            self.assertEqual(geotrackers.basic_auth(), geotrackers.DEFAULT_BASIC_AUTH)

    def test_the_endpoint_is_overridable(self):
        with self.settings(GEOTRACKERS_URL="https://gps.internal/dashboard"):
            self.assertEqual(geotrackers.dashboard_url(), "https://gps.internal/dashboard")
        with self.settings(GEOTRACKERS_URL=""):
            self.assertEqual(geotrackers.dashboard_url(), geotrackers.DEFAULT_DASHBOARD_URL)


class BillableOrdersTests(BaseFleetOpsTest):
    """The Invoices board picks from this list, so it has to be honest about
    what can be billed and why anything cannot."""

    def _delivered_order(self, number, **extra):
        # pod_required defaults to True on Order, which would make every case
        # here report the ePOD reason; the tests that care about it opt in.
        extra.setdefault("pod_required", False)
        order = Order.objects.create(
            number=number, customer=self.customer, pickup=self.pickup, dropoff=self.dropoff,
            weight_kg=12400, distance_km=150, service_rate=self.rate, status="completed",
            completed_at=timezone.now(), **extra)
        order.price_from_rate_card()
        return order

    def test_a_delivered_order_is_listed_with_its_rate_card_amount(self):
        order = self._delivered_order("ORD-BILL-1")
        response = self.client.get("/api/v1/orders/billable/")
        self.assertEqual(response.status_code, 200, response.data)
        row = next(r for r in response.data["orders"] if r["id"] == order.pk)
        self.assertEqual(row["blocked_reason"], "")
        self.assertEqual(row["rate_card"], self.rate.name)
        # 2500 base + 150 km x 48 = 9700 freight, +3.5% surcharge +3300 handling, 5% GST
        self.assertEqual(row["total_amount"], 14006.48)
        self.assertEqual(row["breakdown"]["freight"], 9700.0)

    def test_an_order_awaiting_its_epod_is_listed_with_the_reason(self):
        self._delivered_order("ORD-BILL-2", pod_required=True)   # the model default
        response = self.client.get("/api/v1/orders/billable/")
        row = next(r for r in response.data["orders"] if r["number"] == "ORD-BILL-2")
        self.assertEqual(row["blocked_reason"], "ePOD not verified yet")

    def test_an_order_with_no_rate_card_says_so_rather_than_showing_zero(self):
        Order.objects.create(number="ORD-BILL-3", customer=self.customer, pickup=self.pickup,
                             dropoff=self.dropoff, weight_kg=100, status="completed",
                             completed_at=timezone.now(), pod_required=False)
        response = self.client.get("/api/v1/orders/billable/")
        row = next(r for r in response.data["orders"] if r["number"] == "ORD-BILL-3")
        self.assertEqual(row["blocked_reason"], "No rate card priced against this consignment")

    def test_an_already_invoiced_order_drops_off_the_list(self):
        order = self._delivered_order("ORD-BILL-4")
        self.client.post(f"/api/v1/orders/{order.pk}/invoice/", {}, format="json")
        response = self.client.get("/api/v1/orders/billable/")
        self.assertNotIn("ORD-BILL-4", [r["number"] for r in response.data["orders"]])

    def test_listing_does_not_rewrite_the_stored_freight(self):
        # The preview reprices in memory; a GET must not mutate the order.
        order = self._delivered_order("ORD-BILL-5")
        Order.objects.filter(pk=order.pk).update(freight_amount=1, tax_amount=1, total_amount=1)
        self.client.get("/api/v1/orders/billable/")
        order.refresh_from_db()
        self.assertEqual(order.total_amount, 1)

    def test_an_undelivered_order_is_not_billable(self):
        Order.objects.create(number="ORD-BILL-6", customer=self.customer, pickup=self.pickup,
                             dropoff=self.dropoff, weight_kg=100, status="in_transit",
                             service_rate=self.rate)
        response = self.client.get("/api/v1/orders/billable/")
        self.assertNotIn("ORD-BILL-6", [r["number"] for r in response.data["orders"]])

    def test_billable_orders_sort_ahead_of_blocked_ones(self):
        self._delivered_order("ORD-BILL-9", pod_required=True)   # blocked on ePOD
        self._delivered_order("ORD-BILL-8")                      # billable now
        response = self.client.get("/api/v1/orders/billable/")
        numbers = [r["number"] for r in response.data["orders"]]
        self.assertLess(numbers.index("ORD-BILL-8"), numbers.index("ORD-BILL-9"))
        self.assertEqual(response.data["billable_now"], 1)


class TripViewSetTest(BaseFleetOpsTest):
    """Milk-run: one trip can carry multiple orders."""

    def _make_trip(self):
        r = self.client.post("/api/v1/trips/", {
            "number": "TRP-MLK-01", "vehicle": self.vehicle.id, "driver": self.driver.id,
            "origin": "Bhiwandi", "destination": "Chakan", "planned_departure": "2026-08-01T08:00:00Z",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return Trip.objects.get(pk=r.data["id"])

    def _make_order(self, number):
        order = Order.objects.create(
            number=number, customer=self.customer, pickup=self.pickup, dropoff=self.dropoff,
            weight_kg=5000, status="created")
        return order

    def test_add_order_links_it_to_the_trip(self):
        trip = self._make_trip()
        order = self._make_order("ORD-MLK-01")
        r = self.client.post(f"/api/v1/trips/{trip.id}/add-order/", {"order": order.id}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        linked = [o["id"] for o in r.data["linked_orders"]]
        self.assertIn(order.id, linked)
        order.refresh_from_db()
        self.assertEqual(order.trip_id, trip.id)

    def test_multiple_orders_on_one_trip(self):
        trip = self._make_trip()
        o1 = self._make_order("ORD-MLK-02")
        o2 = self._make_order("ORD-MLK-03")
        self.client.post(f"/api/v1/trips/{trip.id}/add-order/", {"order": o1.id}, format="json")
        r = self.client.post(f"/api/v1/trips/{trip.id}/add-order/", {"order": o2.id}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(r.data["linked_orders"]), 2)

    def test_add_order_already_on_another_trip_is_rejected(self):
        trip1 = self._make_trip()
        r2 = self.client.post("/api/v1/trips/", {
            "number": "TRP-MLK-02", "vehicle": self.vehicle.id, "driver": self.driver.id,
            "origin": "Bhiwandi", "destination": "Chakan", "planned_departure": "2026-08-02T08:00:00Z",
        }, format="json")
        trip2 = Trip.objects.get(pk=r2.data["id"])
        order = self._make_order("ORD-MLK-04")
        order.trip = trip1; order.save(update_fields=["trip"])
        r = self.client.post(f"/api/v1/trips/{trip2.id}/add-order/", {"order": order.id}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_remove_order_unlinks_it(self):
        trip = self._make_trip()
        order = self._make_order("ORD-MLK-05")
        self.client.post(f"/api/v1/trips/{trip.id}/add-order/", {"order": order.id}, format="json")
        r = self.client.post(f"/api/v1/trips/{trip.id}/remove-order/", {"order": order.id}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["linked_orders"], [])
        order.refresh_from_db()
        self.assertIsNone(order.trip_id)

    def test_serializer_exposes_linked_orders(self):
        trip = self._make_trip()
        order = self._make_order("ORD-MLK-06")
        order.trip = trip; order.save(update_fields=["trip"])
        r = self.client.get(f"/api/v1/trips/{trip.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["linked_orders"]), 1)
        self.assertEqual(r.data["linked_orders"][0]["number"], "ORD-MLK-06")
        self.assertEqual(r.data["order_count"], 1)
        self.assertEqual(r.data["lorry_receipt_count"], 0)
        self.assertEqual(r.data["customer_names"], [self.customer.name])


class OrderViewSetTest(BaseFleetOpsTest):
    """ePOD document upload endpoint."""

    def _make_order(self):
        return Order.objects.create(
            number="ORD-POD-01", customer=self.customer, pickup=self.pickup, dropoff=self.dropoff,
            weight_kg=5000, status="in_transit")

    def test_upload_returns_a_url(self):
        from io import BytesIO
        from django.core.files.uploadedfile import SimpleUploadedFile

        order = self._make_order()
        content = b"%PDF-1.4 fake pdf content"
        upload = SimpleUploadedFile("signed-pod.pdf", content, content_type="application/pdf")
        with mock.patch("fleet.storage.store_upload") as store_upload:
            r = self.client.post(
                f"/api/v1/orders/{order.id}/pod-document-upload/",
                {"document": upload},
                format="multipart",
            )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["storage"], "s3")
        self.assertIn(f"/orders/{order.id}/pod-document/", r.data["url"])
        key = store_upload.call_args.args[0]
        self.assertTrue(key.startswith(f"fleet/pod-documents/{order.id}/"))
        self.assertTrue(key.endswith(".pdf"))

        document_name = r.data["url"].rstrip("/").rsplit("/", 1)[-1]
        with mock.patch("fleet.storage.open_file", return_value=BytesIO(content)) as open_file:
            downloaded = self.client.get(r.data["url"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(b"".join(downloaded.streaming_content), content)
        open_file.assert_called_once_with(f"fleet/pod-documents/{order.id}/{document_name}")

    def test_upload_without_file_is_rejected(self):
        order = self._make_order()
        r = self.client.post(f"/api/v1/orders/{order.id}/pod-document-upload/", {}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_upload_rejects_non_document_content(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        order = self._make_order()
        upload = SimpleUploadedFile("payload.exe", b"not a document", content_type="application/octet-stream")
        with mock.patch("fleet.storage.store_upload") as store_upload:
            r = self.client.post(
                f"/api/v1/orders/{order.id}/pod-document-upload/",
                {"document": upload},
                format="multipart",
            )
        self.assertEqual(r.status_code, 400)
        store_upload.assert_not_called()

    def test_s3_failure_is_reported_as_service_unavailable(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from fleet.storage import DocumentStorageError

        order = self._make_order()
        upload = SimpleUploadedFile("signed-pod.pdf", b"%PDF-1.4", content_type="application/pdf")
        with mock.patch("fleet.storage.store_upload", side_effect=DocumentStorageError("S3 is unavailable.")):
            r = self.client.post(
                f"/api/v1/orders/{order.id}/pod-document-upload/",
                {"document": upload},
                format="multipart",
            )
        self.assertEqual(r.status_code, 503)
        self.assertIn("S3 is unavailable", str(r.data))


class InvoiceViewSetTest(AutomaticInvoiceTests):
    """PDF generation for customer invoices."""

    def test_pdf_endpoint_returns_pdf_content(self):
        order = self.create_order()
        self.deliver(order)
        inv_r = self.client.post(f"/api/v1/orders/{order.id}/invoice/", {}, format="json")
        self.assertEqual(inv_r.status_code, 201, inv_r.data)
        invoice_id = inv_r.data["invoice"]["id"]
        r = self.client.get(f"/api/v1/invoices/{invoice_id}/pdf/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_pdf_content_disposition_contains_invoice_number(self):
        order = self.create_order()
        self.deliver(order)
        inv_r = self.client.post(f"/api/v1/orders/{order.id}/invoice/", {}, format="json")
        invoice_id = inv_r.data["invoice"]["id"]
        invoice_number = inv_r.data["invoice"]["number"]
        r = self.client.get(f"/api/v1/invoices/{invoice_id}/pdf/")
        self.assertIn(invoice_number, r.get("Content-Disposition", ""))
        self.assertGreater(len(r.content), 1024)  # a meaningful PDF is at least 1 KB

    @staticmethod
    def _pdf_page_count(pdf_bytes):
        import re
        return len(re.findall(rb"/Type\s*/Page[^s]", pdf_bytes))

    def test_pdf_has_no_annexures_when_there_is_no_lr_or_pod(self):
        # pod_required=False and no pod-request/pod-submit call: genuinely nothing
        # to staple on, unlike deliver() below, which always captures an ePOD.
        order = self.create_order(pod_required=False)
        self.client.post(f"/api/v1/orders/{order.id}/assign/",
                         {"driver": self.driver.id, "vehicle": self.vehicle.id}, format="json")
        self.client.post(f"/api/v1/orders/{order.id}/dispatch/")
        self.client.post(f"/api/v1/orders/{order.id}/complete/", {}, format="json")
        inv_r = self.client.post(f"/api/v1/orders/{order.id}/invoice/", {}, format="json")
        self.assertEqual(inv_r.status_code, 201, inv_r.data)
        invoice_id = inv_r.data["invoice"]["id"]
        r = self.client.get(f"/api/v1/invoices/{invoice_id}/pdf/")
        self.assertEqual(self._pdf_page_count(r.content), 1)

    def test_pdf_staples_the_lr_and_pod_as_further_pages(self):
        """docs: an invoice's PDF carries its own backup - the lorry receipt and
        the verified proof of delivery for every consignment it bills - so the
        customer isn't sent to three separate screens to assemble it by hand."""
        from fleet.lr import build_lr_from_order

        order = self.create_order()
        self.deliver(order)
        build_lr_from_order(order)
        inv_r = self.client.post(f"/api/v1/orders/{order.id}/invoice/", {}, format="json")
        invoice_id = inv_r.data["invoice"]["id"]

        r = self.client.get(f"/api/v1/invoices/{invoice_id}/pdf/")
        self.assertEqual(r.status_code, 200)
        # invoice page + LR annexure page + POD annexure page
        self.assertEqual(self._pdf_page_count(r.content), 3)

    def test_pdf_embeds_an_uploaded_pod_photo(self):
        """The full chain: a real photo streamed to S3 through pod-document-
        upload, attached to the capture, then actually embedded (not just
        referenced by URL) in the invoice PDF's POD annexure - read back from
        the same store the download endpoint itself serves from."""
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image as PILImage

        order = self.create_order()
        self.client.post(f"/api/v1/orders/{order.id}/assign/",
                         {"driver": self.driver.id, "vehicle": self.vehicle.id}, format="json")
        self.client.post(f"/api/v1/orders/{order.id}/dispatch/")

        photo = BytesIO()
        PILImage.new("RGB", (300, 200), color=(60, 120, 90)).save(photo, "JPEG")
        photo_bytes = photo.getvalue()
        upload_file = SimpleUploadedFile("proof.jpg", photo_bytes, content_type="image/jpeg")

        with mock.patch("fleet.storage.store_upload") as store_upload:
            upload = self.client.post(f"/api/v1/orders/{order.id}/pod-document-upload/",
                                      {"document": upload_file}, format="multipart")
        self.assertEqual(upload.status_code, 201, upload.data)
        store_upload.assert_called_once()

        self.client.post(f"/api/v1/orders/{order.id}/pod-submit/",
                         {"receiver_name": "Store manager", "file_url": upload.data["url"]}, format="json")
        self.client.post(f"/api/v1/orders/{order.id}/complete/", {}, format="json")
        inv_r = self.client.post(f"/api/v1/orders/{order.id}/invoice/", {}, format="json")
        self.assertEqual(inv_r.status_code, 201, inv_r.data)
        invoice_id = inv_r.data["invoice"]["id"]

        with mock.patch("fleet.storage.open_file", return_value=BytesIO(photo_bytes)) as open_file:
            r = self.client.get(f"/api/v1/invoices/{invoice_id}/pdf/")
        self.assertEqual(r.status_code, 200)
        open_file.assert_called_once()
        self.assertEqual(self._pdf_page_count(r.content), 2)  # invoice + POD (with the embedded photo)

    def test_pdf_falls_back_to_a_url_reference_when_storage_cannot_be_read(self):
        """A POD referencing this server's own download route, but which S3
        can no longer produce - a deleted object, a transient outage - must
        not break the whole invoice PDF; it degrades to a text reference."""
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile

        order = self.create_order()
        self.client.post(f"/api/v1/orders/{order.id}/assign/",
                         {"driver": self.driver.id, "vehicle": self.vehicle.id}, format="json")
        self.client.post(f"/api/v1/orders/{order.id}/dispatch/")
        upload_file = SimpleUploadedFile("proof.jpg", b"not really a jpeg", content_type="image/jpeg")
        with mock.patch("fleet.storage.store_upload"):
            upload = self.client.post(f"/api/v1/orders/{order.id}/pod-document-upload/",
                                      {"document": upload_file}, format="multipart")
        self.client.post(f"/api/v1/orders/{order.id}/pod-submit/",
                         {"receiver_name": "Store manager", "file_url": upload.data["url"]}, format="json")
        self.client.post(f"/api/v1/orders/{order.id}/complete/", {}, format="json")
        inv_r = self.client.post(f"/api/v1/orders/{order.id}/invoice/", {}, format="json")
        invoice_id = inv_r.data["invoice"]["id"]

        with mock.patch("fleet.storage.open_file", return_value=None):
            r = self.client.get(f"/api/v1/invoices/{invoice_id}/pdf/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._pdf_page_count(r.content), 2)  # invoice + POD (URL reference, no image)

    def test_consolidated_pdf_staples_annexures_for_every_order_on_it(self):
        from fleet.lr import build_lr_from_order

        o1 = self.create_order()
        self.deliver(o1)
        build_lr_from_order(o1)
        o2 = self.create_order()
        self.deliver(o2)
        build_lr_from_order(o2)
        o1.refresh_from_db(); o2.refresh_from_db()
        trip = o1.ensure_trip()
        o2.trip = trip; o2.save(update_fields=["trip"])

        result = self.client.post(f"/api/v1/trips/{trip.id}/consolidate-invoice/", {}, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        invoice_id = result.data["invoices"][0]["id"]

        r = self.client.get(f"/api/v1/invoices/{invoice_id}/pdf/")
        self.assertEqual(r.status_code, 200)
        # invoice page + (LR + POD) annexure pages for each of the 2 orders
        self.assertEqual(self._pdf_page_count(r.content), 5)
