"""Tests for the Fleetbase FleetOps inspired modules."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (ComplianceDocument, Customer, Driver, Fleet, FuelEntry, Issue, MaintenanceSchedule, Order, Place,
                     ProofOfDelivery, ServiceArea, ServiceRate, TripExpense, Vehicle, Vendor, Waypoint, Zone, haversine_km)


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
        self.assertEqual(self.vehicle.status, "on_trip")
        self.assertEqual(self.driver.status, "on_trip")

        completed = self.client.post(f"/api/v1/orders/{order.id}/complete/",
                                     {"receiver_name": "Store manager", "otp": "451209"}, format="json")
        self.assertEqual(completed.status_code, 200)
        order.refresh_from_db(); self.vehicle.refresh_from_db(); self.driver.refresh_from_db()
        self.assertEqual(order.status, "completed")
        self.assertIsNotNone(order.completed_at)
        self.assertEqual(self.vehicle.status, "available")
        self.assertEqual(self.driver.status, "available")
        proof = ProofOfDelivery.objects.get(order=order)
        self.assertTrue(proof.otp_verified)
        self.assertEqual([a.code for a in order.activities.all()][0], "ORDER_COMPLETED")

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

    def test_order_list_supports_filtering_and_search(self):
        first = self.create_order()
        self.create_order(order_type="ptl")
        filtered = self.client.get("/api/v1/orders/", {"order_type": "ptl"})
        self.assertEqual(filtered.data["count"], 1)
        searched = self.client.get("/api/v1/orders/", {"search": first.tracking_number})
        self.assertEqual(searched.data["count"], 1)


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
