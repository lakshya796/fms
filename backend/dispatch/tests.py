"""Tests for the CVRP dispatch planning module.

Two layers, matching `fleet/tests.py`'s convention: solver-level invariant tests
that do not touch the database beyond fixtures (capacity, temperature,
own-vs-outsource), and API-level tests for the collect -> solve -> commit round
trip that actually lands orders on trips.
"""
import sys
from datetime import timedelta
from decimal import Decimal
from importlib.util import find_spec
from unittest import mock, skipUnless

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from fleet.models import Customer, Driver, Indent, Order, Place, ServiceArea, Trip, TripExpense, Vehicle, VehicleHire, Vendor
from iam.models import Role, UserProfile

from .models import CarrierOffer, DispatchPlan, DispatchTask, HireRequirement, PlannedRoute, PlannedStop, PlanVehicle
from .solver import greedy, inputs, matrix, tracking
from .solver.engine import solve_plan
from .solver.replan import replan as replan_plan
from .strategies import resolve_strategy


class BaseDispatchTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("dispatcher", password="test-only-password")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.area = ServiceArea.objects.create(name="West India", code="WEST-D", states="Maharashtra")
        self.customer = Customer.objects.create(name="Test Customer", gstin="27AAACT2727Q1ZW")
        self.pickup = Place.objects.create(name="Bhiwandi warehouse", code="DPL-BHW", city="Bhiwandi",
                                           service_area=self.area, latitude=Decimal("19.296700"), longitude=Decimal("73.063100"))
        self.dropA = Place.objects.create(name="Chakan DC", code="DPL-CKA", city="Chakan",
                                          service_area=self.area, latitude=Decimal("18.760600"), longitude=Decimal("73.863600"))
        self.dropB = Place.objects.create(name="Hinjewadi DC", code="DPL-HNJ", city="Pune",
                                          service_area=self.area, latitude=Decimal("18.591700"), longitude=Decimal("73.738900"))
        self.vehicle = Vehicle.objects.create(registration_number="MH 04 JU 9182", vehicle_type="32 ft MXL",
                                              capacity_kg=8000, volume_cbm=40, current_odometer_km=268400,
                                              current_latitude=Decimal("19.300000"), current_longitude=Decimal("73.060000"))
        self.driver = Driver.objects.create(name="Ramesh Yadav", phone="+919820011223", licence_number="MH0320180001234")
        self.plan = DispatchPlan.objects.create(plan_date=timezone.localdate(), horizon_hours=24)

    def make_order(self, number, dropoff, weight_kg, **extra):
        return Order.objects.create(number=number, customer=self.customer, pickup=self.pickup, dropoff=dropoff,
                                    weight_kg=weight_kg, status="created", **extra)


class TemperatureCompatibilityTests(TestCase):
    def test_dry_cargo_fits_any_vehicle(self):
        self.assertTrue(greedy.temperature_compatible("dry", "dry"))
        self.assertTrue(greedy.temperature_compatible("chiller", "dry"))
        self.assertTrue(greedy.temperature_compatible("frozen", "dry"))

    def test_frozen_cargo_needs_a_frozen_or_multi_unit(self):
        self.assertTrue(greedy.temperature_compatible("frozen", "frozen"))
        self.assertTrue(greedy.temperature_compatible("multi", "frozen"))
        self.assertFalse(greedy.temperature_compatible("chiller", "frozen"))
        self.assertFalse(greedy.temperature_compatible("dry", "frozen"))

    def test_chiller_cargo_can_ride_in_a_frozen_unit(self):
        self.assertTrue(greedy.temperature_compatible("chiller", "chiller"))
        self.assertTrue(greedy.temperature_compatible("frozen", "chiller"))
        self.assertFalse(greedy.temperature_compatible("dry", "chiller"))


class GreedySolverInvariantTests(BaseDispatchTest):
    def _plan_vehicle(self, **overrides):
        defaults = dict(plan=self.plan, vehicle=self.vehicle, source="own",
                        start_latitude=self.vehicle.current_latitude, start_longitude=self.vehicle.current_longitude,
                        available_from=timezone.now(), capacity_kg=8000, capacity_cbm=40, temperature_class="dry",
                        cost_per_km=Decimal("30"), cost_per_hour=Decimal("0"), fixed_cost=Decimal("300"),
                        max_stops=20, max_route_km=800, max_duty_minutes=600)
        defaults.update(overrides)
        return PlanVehicle.objects.create(**defaults)

    def _task(self, dropoff, weight_kg, **overrides):
        defaults = dict(plan=self.plan, pickup=self.pickup, dropoff=dropoff, weight_kg=weight_kg,
                        revenue_estimate=Decimal("5000"), outsource_estimate=Decimal("6000"))
        defaults.update(overrides)
        return DispatchTask.objects.create(**defaults)

    def test_multi_drop_consolidates_onto_one_route_with_a_correct_load_curve(self):
        pv = self._plan_vehicle()
        t1 = self._task(self.dropA, 2000)
        t2 = self._task(self.dropB, 1500)
        routes, outsourced, skipped = greedy.solve([pv], [t1, t2])
        self.assertEqual(outsourced, [])
        self.assertEqual(skipped, [])
        used = [r for r in routes if r.used]
        self.assertEqual(len(used), 1)
        route = used[0]
        load_curve = [s["load_kg"] for s in route.stops]
        self.assertEqual(load_curve, [Decimal("3500"), Decimal("1500"), Decimal("0")])

    def test_a_task_over_capacity_is_never_assigned(self):
        pv = self._plan_vehicle(capacity_kg=1000)
        task = self._task(self.dropA, 2000, outsource_estimate=Decimal("0"))
        routes, outsourced, skipped = greedy.solve([pv], [task])
        self.assertFalse(any(r.used for r in routes))
        self.assertEqual(len(outsourced), 1)
        self.assertEqual(outsourced[0][1], "exceeds weight capacity")

    def test_frozen_cargo_never_lands_on_a_dry_vehicle(self):
        pv = self._plan_vehicle(temperature_class="dry")
        task = self._task(self.dropA, 500, temperature_class="frozen", outsource_estimate=Decimal("0"))
        routes, outsourced, skipped = greedy.solve([pv], [task])
        self.assertFalse(any(r.used for r in routes))
        self.assertEqual(outsourced[0][1], "temperature class mismatch")

    def test_cheaper_to_outsource_when_the_spot_price_beats_the_route_cost(self):
        pv = self._plan_vehicle(cost_per_km=Decimal("1000"))  # deliberately expensive
        task = self._task(self.dropA, 500, outsource_estimate=Decimal("1"))
        routes, outsourced, skipped = greedy.solve([pv], [task])
        self.assertFalse(any(r.used for r in routes))
        self.assertEqual(outsourced[0][1], "cheaper on the spot market")

    def test_must_go_is_served_even_at_a_loss(self):
        pv = self._plan_vehicle(cost_per_km=Decimal("1000"))
        task = self._task(self.dropA, 500, outsource_estimate=Decimal("1"), priority="must_go")
        routes, outsourced, skipped = greedy.solve([pv], [task])
        self.assertTrue(any(r.used for r in routes))
        self.assertEqual(outsourced, [])

    def test_excluded_plan_vehicles_never_receive_work(self):
        pv = self._plan_vehicle(excluded=True, exclusion_reason="Insurance expired")
        task = self._task(self.dropA, 500)
        routes, outsourced, skipped = greedy.solve([pv], [task])
        self.assertFalse(any(r.used for r in routes))
        self.assertEqual(len(outsourced), 1)

    def test_a_task_with_no_coordinates_is_skipped_not_silently_dropped(self):
        bad_place = Place.objects.create(name="No coords", code="DPL-NC", city="Nowhere")
        pv = self._plan_vehicle()
        task = self._task(bad_place, 500)
        routes, outsourced, skipped = greedy.solve([pv], [task])
        self.assertEqual(skipped, [task])
        self.assertFalse(any(r.used for r in routes))


class SolveAndCommitTests(BaseDispatchTest):
    def test_collect_and_solve_serves_a_feasible_order_and_records_a_route(self):
        self.make_order("ORD-DP-1", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, "solved")
        self.assertEqual(self.plan.summary["served_own_fleet"], 1)
        self.assertEqual(PlannedRoute.objects.filter(plan=self.plan).count(), 1)

    def test_commit_creates_one_trip_and_links_the_order(self):
        order = self.make_order("ORD-DP-2", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        self.plan.refresh_from_db()
        route = self.plan.routes.first()
        route.plan_vehicle.driver = self.driver
        route.plan_vehicle.save()

        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/commit/")
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, "assigned")
        self.assertIsNotNone(order.trip_id)
        self.vehicle.refresh_from_db()
        self.assertEqual(self.vehicle.status, "allocated")

    def test_commit_blocks_a_route_with_no_driver_assigned(self):
        self.make_order("ORD-DP-3", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)

        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/commit/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["blocked_routes"]), 1)
        self.assertEqual(len(response.data["committed_routes"]), 0)

    def test_commit_is_idempotent(self):
        order = self.make_order("ORD-DP-4", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        self.plan.refresh_from_db()
        route = self.plan.routes.first()
        route.plan_vehicle.driver = self.driver
        route.plan_vehicle.save()

        first = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/commit/")
        second = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/commit/")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(Trip.objects.filter(vehicle=self.vehicle).count(), 1)

    def test_an_order_with_no_own_capacity_becomes_a_hire_requirement(self):
        # A vehicle far too small forces the order onto the spot market.
        self.vehicle.capacity_kg = 100
        self.vehicle.save()
        self.make_order("ORD-DP-5", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        self.assertEqual(HireRequirement.objects.filter(plan=self.plan).count(), 1)
        self.assertEqual(DispatchTask.objects.filter(plan=self.plan).first().status, "outsourced")


class RoleGatingTests(BaseDispatchTest):
    def test_a_role_without_dispatch_commit_cannot_commit_a_plan(self):
        order = self.make_order("ORD-DP-6", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        self.plan.refresh_from_db()
        route = self.plan.routes.first()
        route.plan_vehicle.driver = self.driver
        route.plan_vehicle.save()

        role = Role.objects.create(name="Planner only", code="planner-only",
                                   permissions=["dispatch.view", "dispatch.plan"])
        UserProfile.objects.create(user=self.user, employee_code="EMP-DP-1", role=role)

        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/commit/")
        self.assertEqual(response.status_code, 400)
        self.plan.refresh_from_db()
        self.assertNotEqual(self.plan.status, "committed")


class FleetBugFixRegressionTests(TestCase):
    """The three defects flagged in docs/DISPATCH-PLANNING.md §3, fixed alongside
    this module because the planner would otherwise inherit all three."""

    def setUp(self):
        self.customer = Customer.objects.create(name="Regress Co", gstin="27AAACT2727Q1ZX")
        self.area = ServiceArea.objects.create(name="Regress Area", code="REG-A")
        self.pickup = Place.objects.create(name="RP", code="REG-P", city="A", service_area=self.area,
                                           latitude=Decimal("19.0"), longitude=Decimal("73.0"))
        self.dropoff = Place.objects.create(name="RD", code="REG-D", city="B", service_area=self.area,
                                            latitude=Decimal("18.5"), longitude=Decimal("73.8"))
        self.vehicle = Vehicle.objects.create(registration_number="MH01AB1234", vehicle_type="32ft", capacity_kg=16000)
        self.driver = Driver.objects.create(name="D1", phone="9000000001", licence_number="LIC-REG-1")

    def test_consolidated_trip_freight_sums_every_order_not_just_the_first(self):
        trip = Trip.objects.create(number="TRP-REG-1", vehicle=self.vehicle, driver=self.driver,
                                   origin="A", destination="B", planned_departure=timezone.now())
        for i in range(3):
            Order.objects.create(number=f"ORD-REG-{i}", customer=self.customer, pickup=self.pickup, dropoff=self.dropoff,
                                 trip=trip, total_amount=Decimal("1000"), status="assigned")
        summary = trip.settlement_summary()
        self.assertEqual(summary["freight"], 3000.0)

    def test_a_vehicle_becoming_free_before_the_order_is_needed_is_a_candidate(self):
        from datetime import timedelta

        from fleet.allocation import recommend_vehicles
        self.vehicle.status = "running"
        self.vehicle.expected_available_at = timezone.now() + timedelta(hours=1)
        self.vehicle.save()
        order = Order.objects.create(number="ORD-REG-AVAIL", customer=self.customer, pickup=self.pickup,
                                     dropoff=self.dropoff, weight_kg=5000, distance_km=100,
                                     scheduled_at=timezone.now() + timedelta(hours=3), status="created")
        candidates = recommend_vehicles(order, include_vendor=False)
        self.assertTrue(any(c["vehicle_id"] == self.vehicle.pk for c in candidates))


class CarrierOfferWorkflowTests(BaseDispatchTest):
    """Phase D: a task the own fleet can't take becomes a HireRequirement, an RFQ
    opens a CarrierOffer per vendor, a recorded quote can be accepted - which
    registers the vendor's truck as a real vehicle, adds it to the plan, and
    re-solves - and a vendor-driven route commits without an internal driver."""

    def setUp(self):
        super().setUp()
        # Too small to take anything - every task in this class is forced to hire.
        self.vehicle.capacity_kg = 100
        self.vehicle.save()
        self.vendor = Vendor.objects.create(name="ACME Transport", code="ACME-CO-1", email="acme@example.com",
                                            vendor_type="transporter", status="active")

    def _solved_requirement(self, order):
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        self.plan.refresh_from_db()
        return HireRequirement.objects.get(plan=self.plan)

    def test_request_quotes_creates_an_invited_offer_per_active_vendor(self):
        order = self.make_order("ORD-CO-1", self.dropA, 2000)
        requirement = self._solved_requirement(order)

        response = self.client.post(f"/api/v1/dispatch/hire-requirements/{requirement.id}/request-quotes/")
        self.assertEqual(response.status_code, 200, response.data)
        offer = CarrierOffer.objects.get(requirement=requirement, vendor=self.vendor)
        self.assertEqual(offer.status, "invited")
        self.assertIsNotNone(offer.message_id)
        requirement.refresh_from_db()
        self.assertEqual(requirement.status, "quoted")

    def test_record_quote_moves_an_offer_to_quoted_with_its_terms(self):
        order = self.make_order("ORD-CO-2", self.dropA, 2000)
        requirement = self._solved_requirement(order)
        self.client.post(f"/api/v1/dispatch/hire-requirements/{requirement.id}/request-quotes/")
        offer = CarrierOffer.objects.get(requirement=requirement, vendor=self.vendor)

        response = self.client.post(f"/api/v1/dispatch/carrier-offers/{offer.id}/record-quote/", {
            "offered_rate": "4500", "rate_basis": "trip", "vehicle_number": "GJ01AB1234",
            "vehicle_type": "32ft", "driver_name": "Vendor Driver", "driver_phone": "9998887777",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        offer.refresh_from_db()
        self.assertEqual(offer.status, "quoted")
        self.assertEqual(offer.offered_rate, Decimal("4500.00"))
        self.assertEqual(offer.vehicle_number, "GJ01AB1234")
        self.assertIsNotNone(offer.responded_at)

    def test_accept_registers_the_vendor_vehicle_and_readds_it_to_the_plan(self):
        order = self.make_order("ORD-CO-3", self.dropA, 2000)
        requirement = self._solved_requirement(order)
        self.client.post(f"/api/v1/dispatch/hire-requirements/{requirement.id}/request-quotes/")
        offer = CarrierOffer.objects.get(requirement=requirement, vendor=self.vendor)
        task = requirement.tasks.first()
        cheap_rate = (task.outsource_estimate / 2) or Decimal("100")
        self.client.post(f"/api/v1/dispatch/carrier-offers/{offer.id}/record-quote/",
                         {"offered_rate": str(cheap_rate), "rate_basis": "trip", "vehicle_number": "GJ01AB1234"}, format="json")

        response = self.client.post(f"/api/v1/dispatch/carrier-offers/{offer.id}/accept/")
        self.assertEqual(response.status_code, 200, response.data)

        offer.refresh_from_db()
        requirement.refresh_from_db()
        self.assertEqual(offer.status, "accepted")
        self.assertEqual(requirement.status, "awarded")
        hired_vehicle = Vehicle.objects.get(registration_number="GJ01AB1234")
        self.assertEqual(hired_vehicle.ownership, "outside")
        self.assertEqual(hired_vehicle.vendor_id, self.vendor.pk)
        self.assertTrue(PlanVehicle.objects.filter(plan=self.plan, vehicle=hired_vehicle, source="hired").exists())
        # The cheap rate beats the generic spot estimate, so the re-solve routes
        # the order onto the newly hired vehicle rather than leaving it outsourced.
        self.assertTrue(PlannedRoute.objects.filter(plan=self.plan, plan_vehicle__vehicle=hired_vehicle,
                                                     stops__task__order=order).exists())

    def test_accepting_rejects_sibling_offers_on_the_same_requirement(self):
        order = self.make_order("ORD-CO-4", self.dropA, 2000)
        requirement = self._solved_requirement(order)
        other_vendor = Vendor.objects.create(name="Other Transport", code="OTHER-CO-1", email="other@example.com")
        self.client.post(f"/api/v1/dispatch/hire-requirements/{requirement.id}/request-quotes/")
        offer = CarrierOffer.objects.get(requirement=requirement, vendor=self.vendor)
        other_offer = CarrierOffer.objects.get(requirement=requirement, vendor=other_vendor)
        task = requirement.tasks.first()
        cheap_rate = (task.outsource_estimate / 2) or Decimal("100")
        self.client.post(f"/api/v1/dispatch/carrier-offers/{offer.id}/record-quote/",
                         {"offered_rate": str(cheap_rate), "rate_basis": "trip", "vehicle_number": "GJ01CD5678"}, format="json")

        self.client.post(f"/api/v1/dispatch/carrier-offers/{offer.id}/accept/")
        other_offer.refresh_from_db()
        self.assertEqual(other_offer.status, "rejected")

    def test_accepted_offer_cannot_be_accepted_twice(self):
        order = self.make_order("ORD-CO-5", self.dropA, 2000)
        requirement = self._solved_requirement(order)
        self.client.post(f"/api/v1/dispatch/hire-requirements/{requirement.id}/request-quotes/")
        offer = CarrierOffer.objects.get(requirement=requirement, vendor=self.vendor)
        task = requirement.tasks.first()
        cheap_rate = (task.outsource_estimate / 2) or Decimal("100")
        self.client.post(f"/api/v1/dispatch/carrier-offers/{offer.id}/record-quote/",
                         {"offered_rate": str(cheap_rate), "rate_basis": "trip", "vehicle_number": "GJ01EF9012"}, format="json")
        self.client.post(f"/api/v1/dispatch/carrier-offers/{offer.id}/accept/")

        second = self.client.post(f"/api/v1/dispatch/carrier-offers/{offer.id}/accept/")
        self.assertEqual(second.status_code, 400)


class VendorDrivenCommitTests(BaseDispatchTest):
    """Unit-level: a route on a vendor-driven PlanVehicle (attached/outside
    ownership, no internal driver) commits without a Trip, in one VehicleHire
    per route - built directly rather than via the solver, so it is deterministic
    regardless of how the greedy solver's own cost comparison happens to fall."""

    def setUp(self):
        super().setUp()
        self.vendor = Vendor.objects.create(name="Direct Vendor", code="DIRECT-1", email="direct@example.com")
        self.hired_vehicle = Vehicle.objects.create(registration_number="RJ14GH3456", vehicle_type="32ft",
                                                    capacity_kg=9000, ownership="outside", vendor=self.vendor,
                                                    status="available")

    def _route_with_order(self, order):
        plan_vehicle = PlanVehicle.objects.create(
            plan=self.plan, vehicle=self.hired_vehicle, source="hired", vendor=self.vendor,
            start_place=self.pickup, available_from=timezone.now(),
            capacity_kg=self.hired_vehicle.capacity_kg, temperature_class="dry",
            cost_per_km=Decimal("0"), fixed_cost=Decimal("4000"))
        task = DispatchTask.objects.create(plan=self.plan, order=order, pickup=self.pickup, dropoff=self.dropA,
                                           weight_kg=order.weight_kg, status="planned")
        route = PlannedRoute.objects.create(plan=self.plan, plan_vehicle=plan_vehicle, sequence=1,
                                            estimated_cost=Decimal("4000"))
        from .models import PlannedStop
        PlannedStop.objects.create(route=route, sequence=1, task=None, place=self.pickup, stop_type="pickup",
                                   planned_arrival=timezone.now(), planned_departure=timezone.now())
        PlannedStop.objects.create(route=route, sequence=2, task=task, place=self.dropA, stop_type="drop",
                                   planned_arrival=timezone.now(), planned_departure=timezone.now())
        self.plan.status = "solved"
        self.plan.save()
        return route

    def test_commit_links_the_order_without_creating_a_trip(self):
        order = self.make_order("ORD-VD-1", self.dropA, 2000)
        self._route_with_order(order)

        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/commit/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["committed_routes"]), 1)
        self.assertEqual(response.data["blocked_routes"], [])

        order.refresh_from_db()
        self.assertEqual(order.status, "assigned")
        self.assertEqual(order.vehicle_id, self.hired_vehicle.pk)
        self.assertEqual(order.vendor_id, self.vendor.pk)
        self.assertIsNone(order.trip_id)
        hire = VehicleHire.objects.get(order=order)
        self.assertEqual(hire.vendor_id, self.vendor.pk)
        self.assertEqual(hire.outside_vehicle_number, self.hired_vehicle.registration_number)
        self.hired_vehicle.refresh_from_db()
        self.assertEqual(self.hired_vehicle.status, "allocated")

    def test_commit_is_idempotent_and_never_double_hires(self):
        order = self.make_order("ORD-VD-2", self.dropA, 2000)
        self._route_with_order(order)

        first = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/commit/")
        second = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/commit/")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(VehicleHire.objects.filter(order=order).count(), 1)

    def test_own_vehicle_route_with_no_driver_is_still_blocked(self):
        order = self.make_order("ORD-VD-3", self.dropA, 2000)
        self.vehicle.capacity_kg = 8000
        self.vehicle.save()
        plan_vehicle = PlanVehicle.objects.create(
            plan=self.plan, vehicle=self.vehicle, source="own", start_place=self.pickup,
            available_from=timezone.now(), capacity_kg=8000, cost_per_km=Decimal("30"), fixed_cost=Decimal("300"))
        task = DispatchTask.objects.create(plan=self.plan, order=order, pickup=self.pickup, dropoff=self.dropA,
                                           weight_kg=order.weight_kg, status="planned")
        route = PlannedRoute.objects.create(plan=self.plan, plan_vehicle=plan_vehicle, sequence=1)
        from .models import PlannedStop
        PlannedStop.objects.create(route=route, sequence=1, task=None, place=self.pickup, stop_type="pickup",
                                   planned_arrival=timezone.now(), planned_departure=timezone.now())
        PlannedStop.objects.create(route=route, sequence=2, task=task, place=self.dropA, stop_type="drop",
                                   planned_arrival=timezone.now(), planned_departure=timezone.now())
        self.plan.status = "solved"
        self.plan.save()

        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/commit/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["blocked_routes"]), 1)
        self.assertEqual(len(response.data["committed_routes"]), 0)


class TravelMatrixLearningTests(TestCase):
    def test_time_bucket_bands_by_two_hours(self):
        self.assertEqual(matrix.time_bucket_for(timezone.make_aware(timezone.datetime(2026, 1, 1, 8, 30))), "08-10")
        self.assertEqual(matrix.time_bucket_for(timezone.make_aware(timezone.datetime(2026, 1, 1, 23, 45))), "22-00")

    def test_a_learned_leg_is_preferred_over_a_fresh_haversine_estimate(self):
        origin, destination = (Decimal("19.0"), Decimal("73.0")), (Decimal("18.5"), Decimal("73.8"))
        at = timezone.now()
        matrix.record_learned_leg(origin, destination, distance_km=Decimal("120.0"), duration_minutes=Decimal("150.0"), at=at)

        distance, duration = matrix.distance_and_duration(origin, destination, depart_at=at)
        self.assertEqual(distance, Decimal("120.00"))
        self.assertEqual(duration, Decimal("150.0"))

    def test_a_second_observation_blends_rather_than_overwrites(self):
        origin, destination = (Decimal("19.1"), Decimal("73.1")), (Decimal("18.6"), Decimal("73.9"))
        at = timezone.now()
        matrix.record_learned_leg(origin, destination, distance_km=Decimal("100"), duration_minutes=Decimal("100"), at=at)
        matrix.record_learned_leg(origin, destination, distance_km=Decimal("100"), duration_minutes=Decimal("200"), at=at)
        distance, duration = matrix.distance_and_duration(origin, destination, depart_at=at)
        self.assertEqual(duration, Decimal("150.0"))


class GpsArrivalCaptureTests(BaseDispatchTest):
    def _committed_route(self, order, *, vehicle=None):
        vehicle = vehicle or self.vehicle
        plan_vehicle = PlanVehicle.objects.create(
            plan=self.plan, vehicle=vehicle, driver=self.driver, source="own", start_place=self.pickup,
            available_from=timezone.now(), capacity_kg=8000, cost_per_km=Decimal("30"), fixed_cost=Decimal("300"))
        task = DispatchTask.objects.create(plan=self.plan, order=order, pickup=self.pickup, dropoff=self.dropA,
                                           weight_kg=order.weight_kg, status="planned")
        trip = Trip.objects.create(number=f"TRP-GPS-{order.number}", vehicle=vehicle, driver=self.driver,
                                   origin="A", destination="B", planned_departure=timezone.now())
        route = PlannedRoute.objects.create(plan=self.plan, plan_vehicle=plan_vehicle, sequence=1, committed_trip=trip)
        pickup_stop = PlannedStop.objects.create(route=route, sequence=1, task=None, place=self.pickup, stop_type="pickup",
                                                 planned_arrival=timezone.now(), planned_departure=timezone.now())
        drop_stop = PlannedStop.objects.create(route=route, sequence=2, task=task, place=self.dropA, stop_type="drop",
                                               planned_arrival=timezone.now(), planned_departure=timezone.now())
        return route, pickup_stop, drop_stop

    def test_arrive_records_variance_against_the_planned_time(self):
        order = self.make_order("ORD-GPS-1", self.dropA, 2000)
        route, pickup_stop, drop_stop = self._committed_route(order)
        pickup_stop.planned_arrival = timezone.now() - timezone.timedelta(minutes=30)
        pickup_stop.save()

        response = self.client.post(f"/api/v1/dispatch/stops/{pickup_stop.id}/arrive/")
        self.assertEqual(response.status_code, 200, response.data)
        pickup_stop.refresh_from_db()
        self.assertIsNotNone(pickup_stop.actual_arrival)
        self.assertGreaterEqual(pickup_stop.variance_minutes, 29)

    def test_arrive_is_idempotent(self):
        order = self.make_order("ORD-GPS-2", self.dropA, 2000)
        route, pickup_stop, drop_stop = self._committed_route(order)
        first = tracking.mark_arrived(pickup_stop)
        second = tracking.mark_arrived(pickup_stop)
        self.assertEqual(first.actual_arrival, second.actual_arrival)

    def test_depart_writes_back_a_learned_leg_between_confirmed_stops(self):
        order = self.make_order("ORD-GPS-3", self.dropA, 2000)
        route, pickup_stop, drop_stop = self._committed_route(order)
        tracking.mark_arrived(pickup_stop, at=timezone.now() - timezone.timedelta(hours=2))
        tracking.mark_departed(pickup_stop, at=timezone.now() - timezone.timedelta(hours=1, minutes=50))

        response = self.client.post(f"/api/v1/dispatch/stops/{drop_stop.id}/depart/")
        self.assertEqual(response.status_code, 200, response.data)
        drop_stop.refresh_from_db()
        self.assertIsNotNone(drop_stop.actual_arrival)
        self.assertIsNotNone(drop_stop.actual_departure)
        from .models import TravelMatrixEntry
        self.assertTrue(TravelMatrixEntry.objects.filter(provider="learned").exists())

    def test_auto_capture_marks_the_next_stop_when_the_vehicle_is_within_radius(self):
        order = self.make_order("ORD-GPS-4", self.dropA, 2000)
        self.vehicle.gps_device_id = "DEV-1"
        self.vehicle.current_latitude = self.pickup.latitude
        self.vehicle.current_longitude = self.pickup.longitude
        self.vehicle.save()
        route, pickup_stop, drop_stop = self._committed_route(order)

        captured = tracking.auto_capture_arrivals(plan=self.plan)
        self.assertEqual(len(captured), 1)
        pickup_stop.refresh_from_db()
        self.assertIsNotNone(pickup_stop.actual_arrival)
        drop_stop.refresh_from_db()
        self.assertIsNone(drop_stop.actual_arrival)  # only the next stop is ever checked

    def test_auto_capture_ignores_a_vehicle_with_no_gps_device(self):
        order = self.make_order("ORD-GPS-5", self.dropA, 2000)
        self.vehicle.current_latitude = self.pickup.latitude
        self.vehicle.current_longitude = self.pickup.longitude
        self.vehicle.save()  # no gps_device_id set
        route, pickup_stop, drop_stop = self._committed_route(order)

        captured = tracking.auto_capture_arrivals(plan=self.plan)
        self.assertEqual(captured, [])

    def test_auto_capture_ignores_a_vehicle_outside_the_radius(self):
        order = self.make_order("ORD-GPS-6", self.dropA, 2000)
        self.vehicle.gps_device_id = "DEV-1"
        self.vehicle.current_latitude = Decimal("28.6")  # Delhi - nowhere near Bhiwandi
        self.vehicle.current_longitude = Decimal("77.2")
        self.vehicle.save()
        route, pickup_stop, drop_stop = self._committed_route(order)

        captured = tracking.auto_capture_arrivals(plan=self.plan)
        self.assertEqual(captured, [])

    def test_routes_needing_replan_flags_a_route_behind_threshold(self):
        order = self.make_order("ORD-GPS-7", self.dropA, 2000)
        route, pickup_stop, drop_stop = self._committed_route(order)
        pickup_stop.planned_arrival = timezone.now() - timezone.timedelta(minutes=90)
        pickup_stop.save()
        tracking.mark_arrived(pickup_stop)

        behind = tracking.routes_needing_replan(self.plan, threshold_minutes=60)
        self.assertEqual(len(behind), 1)
        self.assertEqual(behind[0]["route"], route.id)


class ReplanTests(BaseDispatchTest):
    def test_replan_requires_a_committed_plan(self):
        with self.assertRaises(ValueError):
            replan_plan(self.plan)

    def test_replan_carries_forward_a_not_yet_picked_up_route_and_leaves_a_mid_delivery_route_alone(self):
        order_not_started = self.make_order("ORD-RP-1", self.dropA, 2000)
        order_mid_delivery = self.make_order("ORD-RP-2", self.dropB, 1500)

        vehicle2 = Vehicle.objects.create(registration_number="MH12ZZ0001", vehicle_type="20ft", capacity_kg=6000,
                                          current_latitude=Decimal("18.7"), current_longitude=Decimal("73.8"))
        driver2 = Driver.objects.create(name="Second Driver", phone="9123456780", licence_number="LIC-RP-2")

        pv_not_started = PlanVehicle.objects.create(
            plan=self.plan, vehicle=self.vehicle, driver=self.driver, source="own", start_place=self.pickup,
            available_from=timezone.now(), capacity_kg=8000, cost_per_km=Decimal("30"), fixed_cost=Decimal("300"))
        task1 = DispatchTask.objects.create(plan=self.plan, order=order_not_started, pickup=self.pickup, dropoff=self.dropA,
                                            weight_kg=order_not_started.weight_kg, status="planned")
        trip1 = Trip.objects.create(number="TRP-RP-1", vehicle=self.vehicle, driver=self.driver,
                                    origin="A", destination="B", planned_departure=timezone.now())
        # A real commit() links the order to its trip immediately, well before
        # physical pickup - collect_tasks()'s trip__isnull=True filter is what
        # keeps a plan from re-collecting demand it has already committed.
        order_not_started.trip = trip1
        order_not_started.status = "assigned"
        order_not_started.save(update_fields=["trip", "status"])
        route1 = PlannedRoute.objects.create(plan=self.plan, plan_vehicle=pv_not_started, sequence=1, committed_trip=trip1)
        PlannedStop.objects.create(route=route1, sequence=1, task=None, place=self.pickup, stop_type="pickup",
                                   planned_arrival=timezone.now())  # not yet arrived
        PlannedStop.objects.create(route=route1, sequence=2, task=task1, place=self.dropA, stop_type="drop",
                                   planned_arrival=timezone.now())

        pv_mid = PlanVehicle.objects.create(
            plan=self.plan, vehicle=vehicle2, driver=driver2, source="own", start_place=self.pickup,
            available_from=timezone.now(), capacity_kg=6000, cost_per_km=Decimal("30"), fixed_cost=Decimal("300"))
        task2 = DispatchTask.objects.create(plan=self.plan, order=order_mid_delivery, pickup=self.pickup, dropoff=self.dropB,
                                            weight_kg=order_mid_delivery.weight_kg, status="planned")
        trip2 = Trip.objects.create(number="TRP-RP-2", vehicle=vehicle2, driver=driver2,
                                    origin="A", destination="B", planned_departure=timezone.now())
        order_mid_delivery.trip = trip2
        order_mid_delivery.status = "assigned"
        order_mid_delivery.save(update_fields=["trip", "status"])
        route2 = PlannedRoute.objects.create(plan=self.plan, plan_vehicle=pv_mid, sequence=1, committed_trip=trip2)
        PlannedStop.objects.create(route=route2, sequence=1, task=None, place=self.pickup, stop_type="pickup",
                                   planned_arrival=timezone.now(), actual_arrival=timezone.now())  # already collected
        PlannedStop.objects.create(route=route2, sequence=2, task=task2, place=self.dropB, stop_type="drop",
                                   planned_arrival=timezone.now())

        self.plan.status = "committed"
        self.plan.save()

        child = replan_plan(self.plan, created_by="tester")

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, "superseded")
        route1.refresh_from_db()
        route2.refresh_from_db()
        self.assertTrue(route1.locked)     # carried into the child, superseded here
        self.assertFalse(route2.locked)    # mid-delivery - left running untouched

        self.assertEqual(child.parent_plan_id, self.plan.id)
        carried_orders = set(DispatchTask.objects.filter(plan=child).values_list("order_id", flat=True))
        self.assertIn(order_not_started.id, carried_orders)
        self.assertNotIn(order_mid_delivery.id, carried_orders)
        self.assertEqual(child.status, "solved")


try:
    HAS_ORTOOLS = find_spec("ortools") is not None
except (ImportError, ValueError):     # a broken or shadowed install, not merely a missing one
    HAS_ORTOOLS = False


class OrToolsSolverTests(BaseDispatchTest):
    """Tests for the OR-Tools CVRP+PD solver integration (Phase F).

    `ortools` is deliberately an optional dependency: `ortools_solver` is only
    imported when a plan asks for it, and `engine.solve_plan` falls back to the
    greedy solver when the package is absent. So the tests that need the
    package skip without it, and the fallback - the behaviour every deployment
    without `ortools` installed actually gets - is tested unconditionally.
    """

    def test_a_plan_asking_for_ortools_falls_back_to_greedy_when_it_is_absent(self):
        # Built the same way as the other solve tests, so the own-vs-outsource
        # economics come from the rate card rather than from bare defaults.
        self.make_order("ORD-FALLBACK", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.solver = "ortools"
        self.plan.save()

        # A None entry in sys.modules makes the import raise ImportError, so the
        # fallback is exercised whether or not the package is really installed.
        with mock.patch.dict(sys.modules, {"dispatch.solver.ortools_solver": None}):
            solve_plan(self.plan)

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, "solved")
        self.assertEqual(self.plan.solver, "greedy")     # recorded honestly, not left saying "ortools"
        self.assertEqual(self.plan.summary["served_own_fleet"], 1)

    @skipUnless(HAS_ORTOOLS, "ortools is an optional dependency and is not installed")
    def test_ortools_solver_exposes_the_same_entry_point_as_greedy(self):
        import inspect

        from dispatch.solver import ortools_solver

        parameters = inspect.signature(ortools_solver.solve).parameters
        self.assertIn("plan_vehicles", parameters)
        self.assertIn("tasks", parameters)
        self.assertIn("time_limit_seconds", parameters)
        self.assertIn("horizon_hours", parameters)

    @skipUnless(HAS_ORTOOLS, "ortools is an optional dependency and is not installed")
    def test_ortools_solver_accepts_a_strategy_keyword(self):
        # A real SolveWithParameters() call is heavy native code and, per the
        # class docstring above, deliberately not exercised end-to-end here -
        # this checks the same wiring greedy.solve already has: the entry
        # point takes a strategy and does not require one.
        import inspect

        from dispatch.solver import ortools_solver

        parameters = inspect.signature(ortools_solver.solve).parameters
        self.assertIn("strategy", parameters)
        self.assertIsNone(parameters["strategy"].default)

    def test_engine_resolves_a_strategy_before_dispatching_to_either_solver(self):
        # Cheaper than a real OR-Tools solve: confirm engine.solve_plan hands a
        # resolved Strategy to whichever solver it calls, by intercepting the
        # greedy fallback path (ortools imports but is asked to fall back).
        from dispatch.strategies import Strategy

        self.make_order("ORD-DP-OT-ENGINE", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.solver = "ortools"
        self.plan.save()

        captured = {}
        real_greedy_solve = greedy.solve

        def spy(plan_vehicles, tasks, strategy=None):
            captured["strategy"] = strategy
            return real_greedy_solve(plan_vehicles, tasks, strategy)

        with mock.patch.dict(sys.modules, {"dispatch.solver.ortools_solver": None}), \
             mock.patch("dispatch.solver.engine.greedy_solve", spy):
            solve_plan(self.plan, resolve_strategy({"strategy": "own_fleet_first"}))

        self.assertIsInstance(captured["strategy"], Strategy)
        self.assertEqual(captured["strategy"].preset, "own_fleet_first")


class StrategyTests(BaseDispatchTest):
    """Phase 1 of docs/DISPATCH-PLANNER-V2.md: named planning strategies with
    tunable weights, threaded through the greedy solver and the solve API."""

    def _plan_vehicle(self, **overrides):
        defaults = dict(plan=self.plan, vehicle=self.vehicle, source="own",
                        start_latitude=self.vehicle.current_latitude, start_longitude=self.vehicle.current_longitude,
                        available_from=timezone.now(), capacity_kg=8000, capacity_cbm=40, temperature_class="dry",
                        cost_per_km=Decimal("30"), cost_per_hour=Decimal("0"), fixed_cost=Decimal("300"),
                        max_stops=20, max_route_km=800, max_duty_minutes=600)
        defaults.update(overrides)
        return PlanVehicle.objects.create(**defaults)

    def _task(self, dropoff, weight_kg, **overrides):
        defaults = dict(plan=self.plan, pickup=self.pickup, dropoff=dropoff, weight_kg=weight_kg,
                        revenue_estimate=Decimal("5000"), outsource_estimate=Decimal("6000"))
        defaults.update(overrides)
        return DispatchTask.objects.create(**defaults)

    # -- resolve_strategy -----------------------------------------------

    def test_resolving_no_payload_yields_the_balanced_preset(self):
        from dispatch.strategies import DEFAULT_WEIGHTS, resolve_strategy
        strategy = resolve_strategy(None)
        self.assertEqual(strategy.preset, "balanced")
        self.assertEqual(strategy.weights, DEFAULT_WEIGHTS)

    def test_resolving_a_preset_expands_its_weight_deltas(self):
        from dispatch.strategies import resolve_strategy
        strategy = resolve_strategy({"strategy": "max_utilisation"})
        self.assertEqual(strategy.weights["outsource_bias"], 3.0)
        self.assertEqual(strategy.weights["utilisation_bonus"], 40.0)
        self.assertEqual(strategy.weights["fixed_cost"], 2.0)
        self.assertEqual(strategy.weights["distance_cost"], 1.0)     # untouched default

    def test_weight_overrides_merge_onto_the_preset_rather_than_replacing_it(self):
        from dispatch.strategies import resolve_strategy
        strategy = resolve_strategy({"strategy": "max_utilisation", "weights": {"outsource_bias": 2.5}})
        self.assertEqual(strategy.weights["outsource_bias"], 2.5)         # overridden
        self.assertEqual(strategy.weights["utilisation_bonus"], 40.0)     # preset default retained

    def test_unknown_preset_name_raises(self):
        from dispatch.strategies import StrategyError, resolve_strategy
        with self.assertRaises(StrategyError):
            resolve_strategy({"strategy": "warp_speed"})

    def test_unknown_weight_key_raises(self):
        from dispatch.strategies import StrategyError, resolve_strategy
        with self.assertRaises(StrategyError):
            resolve_strategy({"weights": {"not_a_real_weight": 1}})

    def test_invalid_time_windows_value_raises(self):
        from dispatch.strategies import StrategyError, resolve_strategy
        with self.assertRaises(StrategyError):
            resolve_strategy({"constraints": {"time_windows": "flexible"}})

    def test_strategy_catalogue_lists_every_preset_with_its_full_vector(self):
        from dispatch.strategies import STRATEGY_PRESETS, strategy_catalogue
        rows = strategy_catalogue()
        self.assertEqual({row["name"] for row in rows}, set(STRATEGY_PRESETS))
        max_util = next(row for row in rows if row["name"] == "max_utilisation")
        self.assertEqual(max_util["weights"]["outsource_bias"], 3.0)

    # -- greedy solver is strategy-aware ----------------------------------

    def test_outsource_bias_changes_the_own_vs_market_decision(self):
        """The same load: cheaper to buy under balanced, kept in-house under
        own_fleet_first - the headline behaviour docs/DISPATCH-PLANNER-V2.md §1.1
        says does not exist today."""
        from dispatch.strategies import Strategy, resolve_strategy

        # Learn what this cluster costs to run in-house (force it there with an
        # outsource estimate no real number could beat).
        probe_pv = self._plan_vehicle()
        probe_task = self._task(self.dropA, 2000, outsource_estimate=Decimal("999999"))
        routes, outsourced, skipped = greedy.solve([probe_pv], [probe_task], Strategy())
        self.assertEqual(outsourced, [])
        in_house_cost = routes[0].cost

        # Priced just under that in-house cost, "balanced" buys it on the market...
        balanced_pv = self._plan_vehicle()
        cheap_task = self._task(self.dropA, 2000, outsource_estimate=in_house_cost - Decimal("1"))
        routes, outsourced, skipped = greedy.solve([balanced_pv], [cheap_task], Strategy())
        self.assertEqual(len(outsourced), 1)
        self.assertFalse(routes[0].used)

        # ...but own_fleet_first keeps it in-house even though the market is
        # nominally cheaper - the outsource_bias multiplier and fleet discount win.
        own_first_pv = self._plan_vehicle()
        same_priced_task = self._task(self.dropA, 2000, outsource_estimate=in_house_cost - Decimal("1"))
        routes, outsourced, skipped = greedy.solve([own_first_pv], [same_priced_task],
                                                    resolve_strategy({"strategy": "own_fleet_first"}))
        self.assertEqual(outsourced, [])
        self.assertTrue(routes[0].used)

    def test_max_utilisation_fills_one_route_before_starting_a_second(self):
        """Two clusters, two vehicles, identical geometry - only the fixed-cost
        weight differs between presets, and that alone flips the routing
        decision from two routes to one (docs/DISPATCH-PLANNER-V2.md §9)."""
        from dispatch.strategies import Strategy, resolve_strategy

        p1 = Place.objects.create(name="Strategy P1", code="DPL-SP1", city="P1", service_area=self.area,
                                  latitude=Decimal("17.000000"), longitude=Decimal("73.000000"))
        d1 = Place.objects.create(name="Strategy D1", code="DPL-SD1", city="D1", service_area=self.area,
                                  latitude=Decimal("19.000000"), longitude=Decimal("73.500000"))
        p2 = Place.objects.create(name="Strategy P2", code="DPL-SP2", city="P2", service_area=self.area,
                                  latitude=Decimal("19.100000"), longitude=Decimal("73.500000"))
        d2 = Place.objects.create(name="Strategy D2", code="DPL-SD2", city="D2", service_area=self.area,
                                  latitude=Decimal("19.100000"), longitude=Decimal("74.000000"))

        def make_pv(start_place):
            return self._plan_vehicle(start_latitude=start_place.latitude, start_longitude=start_place.longitude)

        def make_task(pickup, dropoff, revenue):
            return DispatchTask.objects.create(plan=self.plan, pickup=pickup, dropoff=dropoff,
                                               weight_kg=Decimal("4000"), revenue_estimate=Decimal(str(revenue)),
                                               outsource_estimate=Decimal("999999"))

        # C1 outweighs C2 on revenue, so it is always evaluated first - both
        # vehicles start unused, and V1 (at P1) wins it outright regardless of
        # strategy since V2 (at P2) is ~230km away from P1.
        t1 = make_task(p1, d1, 10000)
        t2 = make_task(p2, d2, 100)

        routes, outsourced, skipped = greedy.solve([make_pv(p1), make_pv(p2)], [t1, t2], Strategy())
        self.assertEqual(outsourced, [])
        used = [r for r in routes if r.used]
        self.assertEqual(len(used), 2, "balanced spreads the two clusters across both vehicles")

        strategy = resolve_strategy({"strategy": "max_utilisation"})
        routes2, outsourced2, skipped2 = greedy.solve([make_pv(p1), make_pv(p2)], [t1, t2], strategy)
        self.assertEqual(outsourced2, [])
        used2 = [r for r in routes2 if r.used]
        self.assertEqual(len(used2), 1, "max_utilisation folds C2 onto V1's route instead of starting V2")
        self.assertEqual(len(used2[0].stops), 4)

    def test_hard_time_windows_drop_a_task_that_soft_only_penalises(self):
        from dispatch.strategies import Strategy, resolve_strategy

        soft_pv = self._plan_vehicle()
        soft_task = self._task(self.dropA, 2000, drop_window_end=timezone.now() - timedelta(days=1))
        routes, outsourced, skipped = greedy.solve([soft_pv], [soft_task], Strategy())
        self.assertEqual(outsourced, [])
        self.assertTrue(routes[0].used)     # served late, penalised, not dropped

        hard_pv = self._plan_vehicle()
        hard_task = self._task(self.dropA, 2000, drop_window_end=timezone.now() - timedelta(days=1))
        strategy = resolve_strategy({"constraints": {"time_windows": "hard"}})
        routes2, outsourced2, skipped2 = greedy.solve([hard_pv], [hard_task], strategy)
        self.assertEqual(len(outsourced2), 1)
        self.assertFalse(routes2[0].used)

    def test_allow_partial_service_false_fails_a_load_that_would_be_outsourced(self):
        from dispatch.strategies import resolve_strategy
        pv = self._plan_vehicle(capacity_kg=100)   # too small - the load can only be bought on the market
        task = self._task(self.dropA, 2000, outsource_estimate=Decimal("500"))
        strategy = resolve_strategy({"constraints": {"allow_partial_service": False}})
        with self.assertRaises(RuntimeError):
            greedy.solve([pv], [task], strategy)

    def test_check_constraints_reports_a_breach_without_touching_the_summary(self):
        from dispatch.strategies import check_constraints, resolve_strategy
        strategy = resolve_strategy({"constraints": {"max_outsource_percent": 10}})
        summary = {"total_tasks": 10, "outsourced": 5}
        breaches = check_constraints(strategy, summary)
        self.assertEqual(len(breaches), 1)
        self.assertIn("50.0%", breaches[0])

    # -- API --------------------------------------------------------------

    def test_strategies_endpoint_lists_every_preset(self):
        from dispatch.strategies import STRATEGY_PRESETS
        response = self.client.get("/api/v1/dispatch/strategies/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["name"] for row in response.data}, set(STRATEGY_PRESETS))

    def test_solve_endpoint_rejects_an_unknown_strategy(self):
        self.make_order("ORD-DP-STRAT-BAD", self.dropA, 2000)
        self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/collect/")
        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/solve/",
                                    {"strategy": "warp_speed"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_solve_endpoint_persists_the_chosen_strategy_on_the_plan(self):
        self.make_order("ORD-DP-STRAT-OK", self.dropA, 2000)
        self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/collect/")
        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/solve/",
                                    {"strategy": "max_utilisation"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["objective"]["preset"], "max_utilisation")
        self.assertEqual(response.data["summary"]["strategy"], "max_utilisation")

    def test_solve_endpoint_marks_the_plan_failed_when_a_hard_constraint_blocks_it(self):
        self.vehicle.capacity_kg = 100
        self.vehicle.save()
        self.make_order("ORD-DP-STRAT-FAIL", self.dropA, 2000)
        self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/collect/")
        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/solve/",
                                    {"constraints": {"allow_partial_service": False}}, format="json")
        self.assertEqual(response.status_code, 400)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, "failed")

    def test_max_outsource_percent_is_reported_not_enforced(self):
        from dispatch.strategies import resolve_strategy
        self.vehicle.capacity_kg = 100
        self.vehicle.save()
        self.make_order("ORD-DP-STRAT-BREACH", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan, resolve_strategy({"constraints": {"max_outsource_percent": 10}}))
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, "solved")
        self.assertTrue(self.plan.summary["constraint_breaches"])

    def test_replan_keeps_the_parents_strategy(self):
        from dispatch.strategies import resolve_strategy
        order = self.make_order("ORD-DP-STRAT-REPLAN", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan, resolve_strategy({"strategy": "own_fleet_first"}))
        self.plan.refresh_from_db()
        route = self.plan.routes.first()
        route.plan_vehicle.driver = self.driver
        route.plan_vehicle.save()
        self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/commit/")

        self.plan.refresh_from_db()
        child = replan_plan(self.plan, created_by="dispatcher")
        self.assertEqual(child.objective["preset"], "own_fleet_first")


class DemandCollectionTests(BaseDispatchTest):
    """Phase 2 of docs/DISPATCH-PLANNER-V2.md: collect_tasks reads what the
    order/indent actually recorded - temperature, priority, task type, pickup
    window - instead of hardcoding dry/ftl/normal/no-window on every task."""

    def test_temperature_class_is_read_from_the_order_not_hardcoded(self):
        self.make_order("ORD-DC-1", self.dropA, 2000, temperature_class="frozen", temp_set_point_c=Decimal("-18.0"))
        tasks = inputs.collect_tasks(self.plan)
        self.assertEqual(tasks[0].temperature_class, "frozen")
        self.assertEqual(tasks[0].temp_set_point_c, Decimal("-18.0"))

    def test_a_frozen_order_is_never_routed_onto_a_dry_vehicle(self):
        # self.vehicle defaults temperature_class="dry" - this is the exact
        # defect docs/DISPATCH-PLANNER-V2.md §1.2 (D2) describes.
        order = self.make_order("ORD-DC-2", self.dropA, 2000, temperature_class="frozen")
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        self.plan.refresh_from_db()
        self.assertEqual(HireRequirement.objects.filter(plan=self.plan).count(), 1)
        task = DispatchTask.objects.get(plan=self.plan, order=order)
        self.assertEqual(task.status, "outsourced")

    def test_a_frozen_order_is_routed_onto_a_frozen_vehicle_when_one_exists(self):
        self.vehicle.temperature_class = "frozen"
        self.vehicle.save()
        self.make_order("ORD-DC-3", self.dropA, 2000, temperature_class="frozen")
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.summary["served_own_fleet"], 1)

    def test_order_type_maps_to_task_type(self):
        self.make_order("ORD-DC-4", self.dropA, 2000, order_type="ftl")
        self.make_order("ORD-DC-5", self.dropB, 1000, order_type="ptl")
        tasks = {t.order.number: t for t in inputs.collect_tasks(self.plan)}
        self.assertEqual(tasks["ORD-DC-4"].task_type, "ftl")
        self.assertEqual(tasks["ORD-DC-5"].task_type, "multi_drop_leg")

    def test_an_indent_with_no_order_type_defaults_to_ftl(self):
        Indent.objects.create(number="IND-DC-1", customer=self.customer, pickup=self.pickup,
                              dropoff=self.dropA, weight_kg=Decimal("2000"))
        tasks = inputs.collect_tasks(self.plan)
        self.assertEqual(tasks[0].task_type, "ftl")

    def test_priority_maps_urgent_to_must_go_and_low_to_deferrable(self):
        self.make_order("ORD-DC-6", self.dropA, 2000, priority="urgent")
        self.make_order("ORD-DC-7", self.dropB, 1000, priority="low")
        tasks = {t.order.number: t for t in inputs.collect_tasks(self.plan)}
        self.assertEqual(tasks["ORD-DC-6"].priority, "must_go")
        self.assertEqual(tasks["ORD-DC-7"].priority, "deferrable")

    def test_an_overdue_order_becomes_must_go_even_without_an_explicit_priority(self):
        self.make_order("ORD-DC-8", self.dropA, 2000, scheduled_at=timezone.now() - timedelta(days=1))
        tasks = inputs.collect_tasks(self.plan)
        self.assertEqual(tasks[0].priority, "must_go")

    def test_pickup_window_is_derived_from_the_places_loading_hours(self):
        from dispatch.solver.inputs import _pickup_window
        self.pickup.loading_hours = "09:00-18:00"
        self.pickup.save()
        deadline = timezone.now() + timedelta(days=1)
        self.make_order("ORD-DC-9", self.dropA, 2000, scheduled_at=deadline)
        expected_start, expected_end = _pickup_window(self.pickup, deadline)
        tasks = inputs.collect_tasks(self.plan)
        self.assertEqual(tasks[0].pickup_window_start, expected_start)
        self.assertEqual(tasks[0].pickup_window_end, expected_end)
        self.assertIsNotNone(expected_start)

    def test_an_unparsable_loading_hours_value_degrades_to_no_window(self):
        self.pickup.loading_hours = "business hours"
        self.pickup.save()
        self.make_order("ORD-DC-10", self.dropA, 2000)
        tasks = inputs.collect_tasks(self.plan)
        self.assertIsNone(tasks[0].pickup_window_start)

    def test_a_blank_loading_hours_value_is_no_window(self):
        self.assertEqual(self.pickup.loading_hours, "")
        self.make_order("ORD-DC-10B", self.dropA, 2000)
        tasks = inputs.collect_tasks(self.plan)
        self.assertIsNone(tasks[0].pickup_window_start)
        self.assertIsNone(tasks[0].pickup_window_end)

    def test_a_plant_that_opens_late_makes_the_vehicle_wait_not_load_early(self):
        from dispatch.solver.inputs import _pickup_window
        self.pickup.loading_hours = "09:00-18:00"
        self.pickup.save()
        deadline = timezone.now() + timedelta(days=1)
        self.make_order("ORD-DC-11", self.dropA, 2000, scheduled_at=deadline)
        expected_start, _ = _pickup_window(self.pickup, deadline)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        self.plan.refresh_from_db()
        route = self.plan.routes.first()
        self.assertGreater(route.wait_minutes, 0)
        pickup_stop = route.stops.filter(stop_type="pickup").first()
        self.assertEqual(pickup_stop.planned_arrival, expected_start)
        self.assertEqual(pickup_stop.wait_minutes, route.wait_minutes)

    def test_hard_windows_reject_a_load_whose_pickup_window_has_already_closed(self):
        from dispatch.strategies import resolve_strategy
        self.pickup.loading_hours = "09:00-18:00"
        self.pickup.save()
        # A deadline anchored to a day whose loading window is already long past.
        deadline = timezone.now() - timedelta(days=10)
        order = self.make_order("ORD-DC-12", self.dropA, 2000, scheduled_at=deadline)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan, resolve_strategy({"constraints": {"time_windows": "hard"}}))
        self.plan.refresh_from_db()
        task = DispatchTask.objects.get(plan=self.plan, order=order)
        self.assertIn(task.status, ("outsourced", "dropped"))

    # -- collection filters -------------------------------------------------

    def test_collect_filters_by_customer(self):
        other = Customer.objects.create(name="Other Co", gstin="27AAACT2727Q1ZX")
        self.make_order("ORD-DC-F1", self.dropA, 2000)
        Order.objects.create(number="ORD-DC-F2", customer=other, pickup=self.pickup, dropoff=self.dropA,
                             weight_kg=1000, status="created")
        tasks = inputs.collect_tasks(self.plan, filters={"customers": [self.customer.id]})
        self.assertEqual({t.order.number for t in tasks}, {"ORD-DC-F1"})

    def test_collect_filters_by_temperature_class(self):
        self.make_order("ORD-DC-F3", self.dropA, 2000, temperature_class="dry")
        self.make_order("ORD-DC-F4", self.dropB, 1000, temperature_class="frozen")
        tasks = inputs.collect_tasks(self.plan, filters={"temperature_class": "frozen"})
        self.assertEqual({t.order.number for t in tasks}, {"ORD-DC-F4"})

    def test_collect_filters_by_pickup_place(self):
        third = Place.objects.create(name="Other pickup", code="DPL-OTHER", city="Nashik", service_area=self.area,
                                     latitude=Decimal("19.997500"), longitude=Decimal("73.789700"))
        self.make_order("ORD-DC-F5A", self.dropA, 2000)
        Order.objects.create(number="ORD-DC-F5B", customer=self.customer, pickup=third, dropoff=self.dropA,
                             weight_kg=1000, status="created")
        tasks = inputs.collect_tasks(self.plan, filters={"pickup_places": [self.pickup.id]})
        self.assertEqual({t.order.number for t in tasks}, {"ORD-DC-F5A"})

    def test_explicit_order_ids_overrides_every_other_filter_and_excludes_indents(self):
        order = self.make_order("ORD-DC-F6", self.dropA, 2000)
        self.make_order("ORD-DC-F7", self.dropB, 1000)
        Indent.objects.create(number="IND-DC-F1", customer=self.customer, pickup=self.pickup, dropoff=self.dropA,
                              weight_kg=Decimal("500"))
        tasks = inputs.collect_tasks(self.plan, filters={"order_ids": [order.id]})
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].order_id, order.id)

    def test_include_indents_false_excludes_open_indents(self):
        Indent.objects.create(number="IND-DC-F2", customer=self.customer, pickup=self.pickup, dropoff=self.dropA,
                              weight_kg=Decimal("500"))
        tasks = inputs.collect_tasks(self.plan, filters={"include_indents": False})
        self.assertEqual(tasks, [])

    def test_collect_persists_the_filters_used_onto_the_plan(self):
        inputs.collect_tasks(self.plan, filters={"temperature_class": "frozen"})
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.collection_filters, {"temperature_class": "frozen"})

    def test_collect_endpoint_accepts_filters_in_the_request_body(self):
        self.make_order("ORD-DC-API1", self.dropA, 2000, temperature_class="dry")
        self.make_order("ORD-DC-API2", self.dropB, 1000, temperature_class="frozen")
        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/collect/",
                                    {"temperature_class": "frozen"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["task_count"], 1)
