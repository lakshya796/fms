"""Tests for the CVRP dispatch planning module.

Two layers, matching `fleet/tests.py`'s convention: solver-level invariant tests
that do not touch the database beyond fixtures (capacity, temperature,
own-vs-outsource), and API-level tests for the collect -> solve -> commit round
trip that actually lands orders on trips.
"""
import sys
from datetime import datetime, time, timedelta
from decimal import Decimal
from importlib.util import find_spec
from unittest import mock, skipUnless

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from fleet.models import (Customer, Driver, Indent, Order, Place, ServiceArea, Trip, TripExpense, Vehicle, VehicleHire,
                          Vendor, VendorLaneRate)
from iam.models import Role, UserProfile

from .models import (CarrierOffer, DispatchPlan, DispatchTask, HireRequirement, PlannedRoute, PlannedStop, PlanVehicle,
                     ScenarioProfile)
from .solver import costing, greedy, inputs, matrix, scenarios, tracking
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

    def test_commit_blocks_a_route_with_no_driver_available_to_assign(self):
        # No driver in "available" status - build_plan_vehicles auto-assigns
        # one where it can (docs/DISPATCH-PLANNER-V2.md §8.3), so this is what
        # a genuinely driverless route looks like now.
        self.driver.status = "on_trip"
        self.driver.save()
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
                                 trip=trip, freight_amount=Decimal("1000"), total_amount=Decimal("1000"), status="assigned")
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

        def spy(plan_vehicles, tasks, strategy=None, scenario_profiles=None):
            captured["strategy"] = strategy
            return real_greedy_solve(plan_vehicles, tasks, strategy, scenario_profiles)

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

    def test_indent_type_expected_km_delivery_and_temperature_map_to_task(self):
        delivery_at = timezone.now() + timedelta(days=1)
        Indent.objects.create(number="IND-DC-1", customer=self.customer, pickup=self.pickup,
                              dropoff=self.dropA, weight_kg=Decimal("2000"), indent_type="part_load",
                              expected_running_km=Decimal("321"), expected_delivery_at=delivery_at,
                              temperature_class="chiller", temp_min_c=Decimal("2"), temp_max_c=Decimal("8"))
        tasks = inputs.collect_tasks(self.plan)
        self.assertEqual(tasks[0].task_type, "multi_drop_leg")
        self.assertEqual(tasks[0].temp_set_point_c, Decimal("5"))
        self.assertEqual(tasks[0].drop_window_end, delivery_at)
        self.assertEqual(tasks[0].outsource_estimate,
                         inputs.money(Decimal("321") * costing.spot_rate_for_lane(
                             self.pickup, self.dropA, distance_km=Decimal("321"), vehicle_type="",
                             temperature_class="chiller")[0]))

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
        # Anchor the deadline to a fixed late hour instead of "now + 24h". The
        # vehicle waits for the 09:00 opening, loads, then needs ~3.5h on the
        # road, so a deadline that carried the *current* time of day made this
        # test pass or fail purely on what o'clock the suite ran at: before
        # ~13:00 the drop missed its window, which cost the route a penalty and
        # tipped the load onto the spot market, leaving no route to assert on.
        deadline = timezone.make_aware(
            datetime.combine(timezone.localdate() + timedelta(days=1), time(18, 0)))
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


class KpiTests(BaseDispatchTest):
    """Phase 3 of docs/DISPATCH-PLANNER-V2.md: PlannedRoute.dead_km actually
    gets written, utilisation_volume_percent tells "untracked" apart from
    "empty", and both the route and the plan carry a fuller KPI set."""

    def _solved_plan(self, *orders):
        for spec in orders:
            self.make_order(*spec[:3], **(spec[3] if len(spec) > 3 else {}))
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        self.plan.refresh_from_db()
        return self.plan

    def test_dead_km_is_recorded_on_a_planned_route(self):
        self._solved_plan(("ORD-KPI-1", self.dropA, 2000))
        route = self.plan.routes.first()
        self.assertGreater(route.dead_km, 0)
        self.assertGreaterEqual(route.total_distance_km, route.dead_km)

    def test_utilisation_volume_percent_is_none_when_capacity_is_untracked(self):
        self.vehicle.volume_cbm = 0
        self.vehicle.save()
        self._solved_plan(("ORD-KPI-2", self.dropA, 2000))
        route = self.plan.routes.first()
        self.assertIsNone(route.utilisation_volume_percent)

    def test_utilisation_volume_percent_is_computed_when_capacity_is_tracked(self):
        self._solved_plan(("ORD-KPI-3", self.dropA, 2000, {"volume_cbm": Decimal("10")}))
        route = self.plan.routes.first()
        self.assertIsNotNone(route.utilisation_volume_percent)
        self.assertGreater(route.utilisation_volume_percent, 0)

    def test_route_serializer_exposes_derived_kpis(self):
        self._solved_plan(("ORD-KPI-4", self.dropA, 2000))
        route = self.plan.routes.first()
        response = self.client.get(f"/api/v1/dispatch/routes/{route.id}/")
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertGreater(data["laden_km"], 0)
        self.assertGreaterEqual(data["dead_km_percent"], 0)
        self.assertEqual(data["stop_count"], 2)          # one pickup, one drop
        self.assertEqual(data["orders_carried"], 1)
        self.assertIsNotNone(data["revenue_per_km"])
        self.assertIsNotNone(data["cost_per_tonne_km"])
        self.assertIsNotNone(data["avg_utilisation_percent"])

    def test_on_time_and_window_risk_stops_when_the_task_has_a_deadline(self):
        deadline = timezone.now() + timedelta(days=2)
        self._solved_plan(("ORD-KPI-5", self.dropA, 2000, {"scheduled_at": deadline}))
        route = self.plan.routes.first()
        response = self.client.get(f"/api/v1/dispatch/routes/{route.id}/")
        self.assertEqual(response.data["on_time_stops"], {"on_time": 1, "total": 1})
        self.assertEqual(response.data["window_risk_stops"], 0)   # two days out - no risk

    def test_on_time_stops_is_none_when_no_task_has_a_deadline(self):
        self._solved_plan(("ORD-KPI-6", self.dropA, 2000))
        route = self.plan.routes.first()
        response = self.client.get(f"/api/v1/dispatch/routes/{route.id}/")
        self.assertIsNone(response.data["on_time_stops"])

    def test_plan_summary_includes_the_new_kpi_fields(self):
        self._solved_plan(("ORD-KPI-7", self.dropA, 2000))
        summary = self.plan.summary
        self.assertGreater(summary["total_dead_km"], 0)
        self.assertGreaterEqual(summary["dead_km_percent"], 0)
        self.assertGreater(summary["avg_weight_utilisation"], 0)
        self.assertIn("own_fleet_value", summary)
        self.assertIn("outsourced_value", summary)
        self.assertIn("own_vs_hire_percent", summary)
        self.assertGreater(summary["stops_per_route"], 0)
        self.assertGreater(summary["avg_route_duration_hours"], 0)
        self.assertEqual(summary["tasks_by_temperature"], {"dry": 1})

    def test_own_vs_hire_percent_reflects_a_mixed_plan(self):
        # priority="urgent" -> must_go, so this one is kept in-house even if
        # the market would nominally be cheaper. A different pickup place
        # keeps it in its own cluster - sharing self.pickup with the
        # over-capacity order below would sum their weight into one cluster
        # and outsource both together.
        self.make_order("ORD-KPI-8A", self.dropA, 2000, total_amount=Decimal("15000"), priority="urgent")
        Order.objects.create(number="ORD-KPI-8B", customer=self.customer, pickup=self.dropB, dropoff=self.dropA,
                             weight_kg=50000, status="created")
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        self.plan.refresh_from_db()
        summary = self.plan.summary
        self.assertGreater(summary["own_fleet_value"], 0)
        self.assertGreater(summary["outsourced_value"], 0)
        self.assertLess(summary["own_vs_hire_percent"], 100)

    def test_projected_on_time_percent_is_none_without_any_deadline(self):
        self._solved_plan(("ORD-KPI-9", self.dropA, 2000))
        self.assertIsNone(self.plan.summary["projected_on_time_percent"])

    def test_projected_on_time_percent_reflects_a_met_deadline(self):
        deadline = timezone.now() + timedelta(days=2)
        self._solved_plan(("ORD-KPI-10", self.dropA, 2000, {"scheduled_at": deadline}))
        self.assertEqual(self.plan.summary["projected_on_time_percent"], 100.0)


class PlanViewTests(BaseDispatchTest):
    """Phase 4 of docs/DISPATCH-PLANNER-V2.md: a route cannot be drawn on a map
    without coordinates, which the stop/route shape never carried until now."""

    def _solved_plan(self, *orders):
        for spec in orders:
            self.make_order(*spec[:3], **(spec[3] if len(spec) > 3 else {}))
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        self.plan.refresh_from_db()
        return self.plan

    def test_stop_serializer_exposes_coordinates_and_order_detail(self):
        order = self.make_order("ORD-PV-1", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        route = self.plan.routes.first()
        stop = route.stops.filter(stop_type="drop").first()
        response = self.client.get(f"/api/v1/dispatch/routes/{route.id}/")
        drop = next(s for s in response.data["stops"] if s["stop_type"] == "drop")
        self.assertEqual(float(drop["latitude"]), float(self.dropA.latitude))
        self.assertEqual(float(drop["longitude"]), float(self.dropA.longitude))
        self.assertEqual(drop["city"], self.dropA.city)
        self.assertEqual(drop["order_number"], order.number)
        self.assertEqual(drop["customer_name"], self.customer.name)

    def test_route_path_starts_at_the_vehicle_and_visits_every_stop_in_order(self):
        self._solved_plan(("ORD-PV-2", self.dropA, 2000))
        route = self.plan.routes.first()
        response = self.client.get(f"/api/v1/dispatch/routes/{route.id}/")
        path = response.data["path"]
        self.assertEqual(len(path), 1 + route.stops.count())   # vehicle start + every stop
        self.assertEqual(path[0], [float(self.vehicle.current_latitude), float(self.vehicle.current_longitude)])
        last_stop = route.stops.order_by("-sequence").first()
        self.assertEqual(path[-1], [float(last_stop.place.latitude), float(last_stop.place.longitude)])

    def test_map_endpoint_returns_a_coloured_route_and_no_unrouted_tasks(self):
        self._solved_plan(("ORD-PV-3", self.dropA, 2000))
        response = self.client.get(f"/api/v1/dispatch/plans/{self.plan.id}/map/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["routes"]), 1)
        self.assertTrue(response.data["routes"][0]["colour"].startswith("#"))
        self.assertGreater(len(response.data["routes"][0]["path"]), 0)
        self.assertEqual(response.data["unrouted_tasks"], [])

    def test_map_endpoint_surfaces_an_outsourced_task_with_pickup_and_drop_coordinates(self):
        # Too small a vehicle forces the load onto the market instead of a route.
        self.vehicle.capacity_kg = 100
        self.vehicle.save()
        self._solved_plan(("ORD-PV-4", self.dropA, 2000))
        response = self.client.get(f"/api/v1/dispatch/plans/{self.plan.id}/map/")
        self.assertEqual(response.data["routes"], [])
        self.assertEqual(len(response.data["unrouted_tasks"]), 1)
        unrouted = response.data["unrouted_tasks"][0]
        self.assertEqual(unrouted["status"], "outsourced")
        self.assertEqual(unrouted["order_number"], "ORD-PV-4")
        self.assertEqual(unrouted["pickup_lat"], float(self.pickup.latitude))
        self.assertEqual(unrouted["dropoff_lat"], float(self.dropA.latitude))

    def test_map_endpoint_assigns_distinct_colours_to_multiple_routes(self):
        second_vehicle = Vehicle.objects.create(registration_number="MH 12 QR 5566", vehicle_type="20 ft SXL",
                                                 capacity_kg=8000, volume_cbm=40, current_latitude=Decimal("18.590000"),
                                                 current_longitude=Decimal("73.730000"))
        Order.objects.create(number="ORD-PV-5A", customer=self.customer, pickup=self.pickup, dropoff=self.dropA,
                             weight_kg=2000, status="created")
        Order.objects.create(number="ORD-PV-5B", customer=self.customer, pickup=self.dropB, dropoff=self.dropA,
                             weight_kg=2000, status="created")
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        response = self.client.get(f"/api/v1/dispatch/plans/{self.plan.id}/map/")
        colours = {route["colour"] for route in response.data["routes"]}
        self.assertEqual(len(colours), len(response.data["routes"]))   # every route gets its own colour


class PinnedVehicleTests(BaseDispatchTest):
    """Phase 6 of docs/DISPATCH-PLANNER-V2.md: a dispatcher's pin is honoured
    by the next solve instead of being just a suggestion."""

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

    def test_pinned_task_stays_on_its_vehicle_even_when_the_market_is_nominally_cheaper(self):
        pv = self._plan_vehicle()
        task = self._task(self.dropA, 2000, outsource_estimate=Decimal("1"), pinned_vehicle=self.vehicle)
        routes, outsourced, skipped = greedy.solve([pv], [task])
        self.assertEqual(outsourced, [])
        self.assertTrue(routes[0].used)

    def test_pin_to_a_vehicle_not_in_the_plan_is_outsourced_rather_than_ignored(self):
        other_vehicle = Vehicle.objects.create(registration_number="MH 20 XX 1111", vehicle_type="20 ft SXL", capacity_kg=8000)
        pv = self._plan_vehicle()
        task = self._task(self.dropA, 2000, pinned_vehicle=other_vehicle)
        routes, outsourced, skipped = greedy.solve([pv], [task])
        self.assertEqual(len(outsourced), 1)
        self.assertFalse(routes[0].used)

    def test_conflicting_pins_in_the_same_cluster_are_outsourced(self):
        other_vehicle = Vehicle.objects.create(registration_number="MH 20 XX 2222", vehicle_type="20 ft SXL",
                                               capacity_kg=8000, current_latitude=Decimal("18.590000"),
                                               current_longitude=Decimal("73.730000"))
        pv1 = self._plan_vehicle()
        pv2 = self._plan_vehicle(vehicle=other_vehicle, start_latitude=other_vehicle.current_latitude,
                                 start_longitude=other_vehicle.current_longitude)
        t1 = self._task(self.dropA, 1000, pinned_vehicle=self.vehicle)
        t2 = self._task(self.dropA, 1000, pinned_vehicle=other_vehicle)   # shares t1's pickup -> same cluster
        routes, outsourced, skipped = greedy.solve([pv1, pv2], [t1, t2])
        self.assertEqual(len(outsourced), 1)
        self.assertIn("conflicting", outsourced[0][1])


class DriverAssignmentTests(BaseDispatchTest):
    """Phase 6: build_plan_vehicles assigns a driver per vehicle instead of
    leaving every route driverless by construction (docs/DISPATCH-PLANNER-V2.md §8.3)."""

    def test_build_plan_vehicles_assigns_an_available_driver(self):
        plan_vehicles = inputs.build_plan_vehicles(self.plan)
        self.assertEqual(plan_vehicles[0].driver_id, self.driver.id)

    def test_build_plan_vehicles_does_not_double_book_a_driver(self):
        Vehicle.objects.create(registration_number="MH 12 QR 7777", vehicle_type="20 ft SXL", capacity_kg=8000,
                               current_latitude=self.vehicle.current_latitude, current_longitude=self.vehicle.current_longitude)
        plan_vehicles = inputs.build_plan_vehicles(self.plan)
        driver_ids = [pv.driver_id for pv in plan_vehicles if pv.driver_id]
        self.assertEqual(len(driver_ids), len(set(driver_ids)))

    def test_build_plan_vehicles_skips_a_driver_with_an_expired_licence(self):
        self.driver.licence_expiry = timezone.localdate() - timedelta(days=1)
        self.driver.save()
        plan_vehicles = inputs.build_plan_vehicles(self.plan)
        self.assertIsNone(plan_vehicles[0].driver_id)

    def test_build_plan_vehicles_skips_a_driver_who_is_not_available(self):
        self.driver.status = "on_trip"
        self.driver.save()
        plan_vehicles = inputs.build_plan_vehicles(self.plan)
        self.assertIsNone(plan_vehicles[0].driver_id)


class ManualOverrideTests(BaseDispatchTest):
    """Phase 6: move/reorder/pin/unroute-task, with an immediate re-cost of
    every route touched."""

    def _two_route_plan(self):
        second_vehicle = Vehicle.objects.create(registration_number="MH 12 QR 8888", vehicle_type="20 ft SXL", capacity_kg=8000,
                                                volume_cbm=40, current_latitude=Decimal("18.590000"), current_longitude=Decimal("73.730000"))
        Order.objects.create(number="ORD-MO-1", customer=self.customer, pickup=self.pickup, dropoff=self.dropA,
                             weight_kg=2000, status="created")
        order2 = Order.objects.create(number="ORD-MO-2", customer=self.customer, pickup=self.dropB, dropoff=self.dropA,
                                      weight_kg=2000, status="created")
        inputs.collect_tasks(self.plan)
        # Pin order2's task onto the second vehicle so the two orders land on
        # two distinct routes deterministically, rather than depending on
        # which arrangement the cost comparison happens to favour.
        DispatchTask.objects.filter(plan=self.plan, order=order2).update(pinned_vehicle=second_vehicle)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        self.plan.refresh_from_db()
        return list(self.plan.routes.order_by("sequence"))

    def test_move_task_endpoint_moves_a_task_and_recosts_both_routes(self):
        routes = self._two_route_plan()
        self.assertEqual(len(routes), 2)
        from_route, to_route = routes[0], routes[1]
        task = from_route.stops.filter(stop_type="drop").first().task
        original_to_route_stop_count = to_route.stops.count()

        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/move-task/",
                                    {"task": task.id, "to_route": to_route.id}, format="json")
        self.assertEqual(response.status_code, 200, response.data)

        to_route.refresh_from_db()
        self.assertEqual(to_route.stops.count(), original_to_route_stop_count + 2)   # its own pickup + drop
        self.assertTrue(to_route.stops.filter(task=task, stop_type="drop").exists())
        self.assertFalse(PlannedStop.objects.filter(route=from_route, task=task).exists())
        task.refresh_from_db()
        self.assertEqual(task.status, "planned")

    def test_unroute_task_endpoint_frees_the_task_and_recosts_its_route(self):
        routes = self._two_route_plan()
        route = routes[0]
        task = route.stops.filter(stop_type="drop").first().task
        original_cost = route.estimated_cost

        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/unroute-task/",
                                    {"task": task.id}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        task.refresh_from_db()
        self.assertEqual(task.status, "pending")
        self.assertFalse(PlannedStop.objects.filter(route=route, task=task).exists())
        route.refresh_from_db()
        self.assertNotEqual(route.estimated_cost, original_cost)

    def test_reorder_route_endpoint_changes_sequence_and_recosts(self):
        routes = self._two_route_plan()
        route = routes[0]
        stop_ids = list(route.stops.order_by("sequence").values_list("id", flat=True))
        reversed_ids = list(reversed(stop_ids))

        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/reorder-route/",
                                    {"route": route.id, "stop_ids": reversed_ids}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        new_order = list(PlannedStop.objects.filter(route=route).order_by("sequence").values_list("id", flat=True))
        self.assertEqual(new_order, reversed_ids)

    def test_reorder_route_rejects_a_stop_id_list_that_does_not_match(self):
        routes = self._two_route_plan()
        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/reorder-route/",
                                    {"route": routes[0].id, "stop_ids": [999999]}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_pin_task_endpoint_sets_and_clears_the_pin(self):
        self.make_order("ORD-MO-PIN", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        task = DispatchTask.objects.get(plan=self.plan)

        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/pin-task/",
                                    {"task": task.id, "vehicle": self.vehicle.id}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        task.refresh_from_db()
        self.assertEqual(task.pinned_vehicle_id, self.vehicle.id)

        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/pin-task/", {"task": task.id}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        task.refresh_from_db()
        self.assertIsNone(task.pinned_vehicle_id)

    def test_move_task_rejects_a_task_from_a_different_plan(self):
        other_plan = DispatchPlan.objects.create(plan_date=timezone.localdate())
        other_task = DispatchTask.objects.create(plan=other_plan, pickup=self.pickup, dropoff=self.dropA, weight_kg=1000)
        routes = self._two_route_plan()
        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/move-task/",
                                    {"task": other_task.id, "to_route": routes[0].id}, format="json")
        self.assertEqual(response.status_code, 400)


class ScenarioComparisonTests(BaseDispatchTest):
    """Phase 6: solve the same demand under several strategies, compare, and
    adopt the winner - see docs/DISPATCH-PLANNER-V2.md §8.2."""

    def test_compare_endpoint_creates_one_scenario_per_strategy(self):
        self.make_order("ORD-SC-1", self.dropA, 2000)
        self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/collect/")
        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/compare/",
                                    {"strategies": ["least_cost", "own_fleet_first"]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["scenarios"]), 2)
        self.assertEqual({s["strategy"] for s in response.data["scenarios"]}, {"least_cost", "own_fleet_first"})
        self.assertEqual(DispatchPlan.objects.filter(parent_plan=self.plan, is_scenario=True).count(), 2)

    def test_compare_rejects_an_unknown_strategy(self):
        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/compare/",
                                    {"strategies": ["warp_speed"]}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_adopt_transplants_the_scenarios_routes_onto_the_parent(self):
        self.make_order("ORD-SC-2", self.dropA, 2000)
        self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/collect/")
        compare_response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/compare/",
                                            {"strategies": ["balanced"]}, format="json")
        scenario_id = compare_response.data["scenarios"][0]["id"]

        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/adopt/", {"scenario": scenario_id}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, "solved")
        self.assertEqual(self.plan.routes.count(), 1)
        self.assertEqual(PlannedRoute.objects.filter(plan_id=scenario_id).count(), 0)
        self.assertEqual(DispatchPlan.objects.get(pk=scenario_id).status, "superseded")

    def test_adopt_marks_every_other_scenario_superseded(self):
        self.make_order("ORD-SC-3", self.dropA, 2000)
        self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/collect/")
        compare_response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/compare/",
                                            {"strategies": ["balanced", "least_cost"]}, format="json")
        winner_id = compare_response.data["scenarios"][0]["id"]
        loser_id = compare_response.data["scenarios"][1]["id"]

        self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/adopt/", {"scenario": winner_id}, format="json")
        self.assertEqual(DispatchPlan.objects.get(pk=loser_id).status, "superseded")

    def test_adopt_rejects_a_scenario_that_does_not_belong_to_this_plan(self):
        other_plan = DispatchPlan.objects.create(plan_date=timezone.localdate())
        foreign_scenario = DispatchPlan.objects.create(plan_date=timezone.localdate(), parent_plan=other_plan, is_scenario=True)
        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/adopt/",
                                    {"scenario": foreign_scenario.id}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_adopt_is_blocked_once_the_plan_is_committed(self):
        self.plan.status = "committed"
        self.plan.save()
        scenario = DispatchPlan.objects.create(plan_date=timezone.localdate(), parent_plan=self.plan, is_scenario=True)
        response = self.client.post(f"/api/v1/dispatch/plans/{self.plan.id}/adopt/",
                                    {"scenario": scenario.id}, format="json")
        self.assertEqual(response.status_code, 400)


class LaneSpotPricingTests(BaseDispatchTest):
    """Phase 5 of docs/DISPATCH-PLANNER-V2.md: lane-level spot pricing instead
    of one national rate for every lane, vehicle type and season."""

    def setUp(self):
        super().setUp()
        self.vendor = Vendor.objects.create(name="Prime Carriers", code="VEN-PRIME", vendor_type="transporter", status="active")
        self.pickup.state = "Maharashtra"; self.pickup.save()
        self.dropA.state = "Maharashtra"; self.dropA.save()

    def _hire(self, pickup, dropoff, rate, **extra):
        order = Order.objects.create(number=f"ORD-HIRE-{Order.objects.count() + 1}", customer=self.customer,
                                     pickup=pickup, dropoff=dropoff, weight_kg=5000, status="completed")
        return VehicleHire.objects.create(order=order, vendor=self.vendor, rate_basis="km",
                                          agreed_rate=Decimal(str(rate)), status="confirmed", **extra)

    def test_fallback_confidence_when_no_history_or_contract_exists(self):
        costing.reset_cache()
        rate, confidence = costing.spot_rate_for_lane(self.pickup, self.dropA)
        self.assertEqual(confidence, "fallback")
        self.assertGreater(rate, 0)

    def test_lane_history_beats_the_fallback(self):
        self._hire(self.pickup, self.dropA, 25)
        self._hire(self.pickup, self.dropA, 35)
        costing.reset_cache()
        rate, confidence = costing.spot_rate_for_lane(self.pickup, self.dropA)
        self.assertEqual(confidence, "lane")
        self.assertEqual(rate, Decimal("30.00"))   # median of 25 and 35

    def test_corridor_history_used_when_no_exact_lane_history_exists(self):
        other_pickup = Place.objects.create(name="Nashik plant", code="DPL-NSK", city="Nashik", state="Maharashtra",
                                            service_area=self.area, latitude=Decimal("19.997500"), longitude=Decimal("73.789700"))
        self._hire(other_pickup, self.dropA, 28)
        costing.reset_cache()
        rate, confidence = costing.spot_rate_for_lane(self.pickup, self.dropA)
        self.assertEqual(confidence, "corridor")
        self.assertEqual(rate, Decimal("28.00"))

    def test_vehicle_type_average_used_when_only_that_history_exists(self):
        other_pickup = Place.objects.create(name="Delhi hub", code="DPL-DEL", city="Delhi", state="Delhi",
                                            service_area=self.area, latitude=Decimal("28.644800"), longitude=Decimal("77.216700"))
        other_dropoff = Place.objects.create(name="Jaipur DC", code="DPL-JAI", city="Jaipur", state="Rajasthan",
                                             service_area=self.area, latitude=Decimal("26.912400"), longitude=Decimal("75.787300"))
        self._hire(other_pickup, other_dropoff, 22, outside_vehicle_type="32 ft MXL")
        costing.reset_cache()
        rate, confidence = costing.spot_rate_for_lane(self.pickup, self.dropA, vehicle_type="32 ft MXL")
        self.assertEqual(confidence, "type")
        self.assertEqual(rate, Decimal("22.00"))

    def test_contract_rate_beats_history(self):
        self._hire(self.pickup, self.dropA, 25)
        VendorLaneRate.objects.create(vendor=self.vendor, origin_city=self.pickup.city, destination_city=self.dropA.city,
                                      rate=Decimal("40"), rate_basis="km", active=True)
        costing.reset_cache()
        rate, confidence = costing.spot_rate_for_lane(self.pickup, self.dropA)
        self.assertEqual(confidence, "contract")
        self.assertEqual(rate, Decimal("40.00"))

    def test_an_expired_contract_rate_is_ignored(self):
        VendorLaneRate.objects.create(vendor=self.vendor, origin_city=self.pickup.city, destination_city=self.dropA.city,
                                      rate=Decimal("999"), rate_basis="km", active=True,
                                      valid_until=timezone.localdate() - timedelta(days=1))
        costing.reset_cache()
        rate, confidence = costing.spot_rate_for_lane(self.pickup, self.dropA)
        self.assertNotEqual(confidence, "contract")

    def test_a_trip_basis_contract_rate_is_converted_using_distance(self):
        VendorLaneRate.objects.create(vendor=self.vendor, origin_city=self.pickup.city, destination_city=self.dropA.city,
                                      rate=Decimal("3000"), rate_basis="trip", active=True)
        costing.reset_cache()
        rate, confidence = costing.spot_rate_for_lane(self.pickup, self.dropA, distance_km=Decimal("100"))
        self.assertEqual(confidence, "contract")
        self.assertEqual(rate, Decimal("30.00"))

    def test_collect_tasks_records_the_confidence_on_the_task(self):
        self._hire(self.pickup, self.dropA, 25)
        self.make_order("ORD-LANE-1", self.dropA, 2000)
        tasks = inputs.collect_tasks(self.plan)
        self.assertEqual(tasks[0].outsource_confidence, "lane")


class SpotSlotVehicleTests(BaseDispatchTest):
    """Phase 5: hired capacity as a routable candidate, not just a per-task
    outsource price - a spot-slot PlanVehicle can carry a multi-drop the same
    way an own vehicle can."""

    def setUp(self):
        super().setUp()
        self.vendor = Vendor.objects.create(name="Prime Carriers", code="VEN-PRIME", vendor_type="transporter", status="active")

    def test_no_spot_slots_without_any_matching_vendor_lane_rate(self):
        self.make_order("ORD-SS-1", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        self.assertEqual(inputs.build_spot_slot_vehicles(self.plan), [])

    def test_creates_a_plan_vehicle_for_a_matching_active_lane_rate(self):
        VendorLaneRate.objects.create(vendor=self.vendor, origin_city=self.pickup.city, destination_city=self.dropA.city,
                                      rate=Decimal("32"), rate_basis="km", active=True)
        self.make_order("ORD-SS-2", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        slots = inputs.build_spot_slot_vehicles(self.plan)
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0].source, "spot_slot")
        self.assertIsNone(slots[0].vehicle_id)
        self.assertEqual(slots[0].cost_per_km, Decimal("32.00"))

    def test_ignores_a_lane_rate_for_a_pickup_city_with_no_demand_in_this_plan(self):
        VendorLaneRate.objects.create(vendor=self.vendor, origin_city="Nagpur", destination_city=self.dropA.city,
                                      rate=Decimal("32"), rate_basis="km", active=True)
        self.make_order("ORD-SS-3", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        self.assertEqual(inputs.build_spot_slot_vehicles(self.plan), [])

    def test_ignores_an_inactive_rate(self):
        VendorLaneRate.objects.create(vendor=self.vendor, origin_city=self.pickup.city, destination_city=self.dropA.city,
                                      rate=Decimal("32"), rate_basis="km", active=False)
        self.make_order("ORD-SS-4", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        self.assertEqual(inputs.build_spot_slot_vehicles(self.plan), [])

    def test_ignores_an_expired_rate(self):
        VendorLaneRate.objects.create(vendor=self.vendor, origin_city=self.pickup.city, destination_city=self.dropA.city,
                                      rate=Decimal("32"), rate_basis="km", active=True,
                                      valid_until=timezone.localdate() - timedelta(days=1))
        self.make_order("ORD-SS-5", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        self.assertEqual(inputs.build_spot_slot_vehicles(self.plan), [])

    def test_spot_slot_vehicle_can_be_routed_by_the_solver(self):
        self.vehicle.status = "under_maintenance"   # no own vehicle can serve this
        self.vehicle.save()
        VendorLaneRate.objects.create(vendor=self.vendor, origin_city=self.pickup.city, destination_city=self.dropA.city,
                                      rate=Decimal("20"), rate_basis="km", active=True)
        self.make_order("ORD-SS-6", self.dropA, 2000)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        inputs.build_spot_slot_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.summary["served_own_fleet"], 1)
        route = self.plan.routes.first()
        self.assertEqual(route.plan_vehicle.source, "spot_slot")


class ScenarioMatchingTests(BaseDispatchTest):
    """docs/SCENARIO-PROFILES.md: `Cluster`'s matching inputs and
    `solver.scenarios`' pure matching/merging functions, independent of the
    solver loop that calls them."""

    def _task(self, dropoff, weight_kg, **overrides):
        defaults = dict(plan=self.plan, pickup=self.pickup, dropoff=dropoff, weight_kg=weight_kg,
                        revenue_estimate=Decimal("5000"), outsource_estimate=Decimal("6000"))
        defaults.update(overrides)
        return DispatchTask.objects.create(**defaults)

    def _profile(self, **overrides):
        defaults = dict(name="Test Profile")
        defaults.update(overrides)
        return ScenarioProfile.objects.create(**defaults)

    def test_a_cluster_with_two_tasks_sharing_a_pickup_has_two_drops(self):
        t1 = self._task(self.dropA, 1000)
        t2 = self._task(self.dropB, 1000)
        clusters, skipped = greedy.build_clusters([t1, t2])
        self.assertEqual(skipped, [])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0].tasks), 2)

    def test_cluster_distance_km_is_the_furthest_drop(self):
        t1 = self._task(self.dropA, 1000)
        t2 = self._task(self.dropB, 1000)
        clusters, skipped = greedy.build_clusters([t1, t2])
        km_a, _ = matrix.distance_and_duration((self.pickup.latitude, self.pickup.longitude),
                                                (self.dropA.latitude, self.dropA.longitude))
        km_b, _ = matrix.distance_and_duration((self.pickup.latitude, self.pickup.longitude),
                                                (self.dropB.latitude, self.dropB.longitude))
        self.assertEqual(clusters[0].distance_km, max(km_a, km_b))

    def test_same_city_true_only_when_every_drop_matches_the_pickup_city(self):
        same_city_drop = Place.objects.create(name="Bhiwandi Annex", code="DPL-BHA", city=self.pickup.city,
                                              service_area=self.area, latitude=Decimal("19.300000"), longitude=Decimal("73.070000"))
        local_task = self._task(same_city_drop, 1000)
        local_clusters, _ = greedy.build_clusters([local_task])
        self.assertTrue(local_clusters[0].same_city)

        distant_task = self._task(self.dropA, 1000)   # Chakan, not Bhiwandi
        distant_clusters, _ = greedy.build_clusters([distant_task])
        self.assertFalse(distant_clusters[0].same_city)

    def test_match_profile_respects_priority_order(self):
        loose = self._profile(name="Loose", priority=50)          # no criteria - matches anything
        tight = self._profile(name="Tight", priority=10, match_min_drops=2)
        t1 = self._task(self.dropA, 1000)
        t2 = self._task(self.dropB, 1000)
        clusters, _ = greedy.build_clusters([t1, t2])
        matched = scenarios.match_profile(clusters[0], ScenarioProfile.objects.filter(active=True))
        self.assertEqual(matched, tight)
        self.assertNotEqual(matched, loose)

    def test_match_profile_returns_none_when_nothing_matches(self):
        self._profile(name="Reefer only", match_temperature_classes=["frozen"])
        task = self._task(self.dropA, 1000, temperature_class="dry")
        clusters, _ = greedy.build_clusters([task])
        matched = scenarios.match_profile(clusters[0], ScenarioProfile.objects.filter(active=True))
        self.assertIsNone(matched)

    def test_effective_strategy_merges_overrides_onto_the_base(self):
        from dispatch.strategies import Strategy
        base = Strategy()
        profile = self._profile(weight_overrides={"outsource_bias": 5.0}, constraint_overrides={"time_windows": "hard"})
        effective = scenarios.effective_strategy(base, profile)
        self.assertEqual(effective.weights["outsource_bias"], 5.0)
        self.assertEqual(effective.weights["distance_cost"], base.weights["distance_cost"])   # untouched
        self.assertEqual(effective.constraints["time_windows"], "hard")

    def test_effective_strategy_with_no_matched_profile_returns_the_base_unchanged(self):
        from dispatch.strategies import Strategy
        base = Strategy()
        self.assertIs(scenarios.effective_strategy(base, None), base)


class ScenarioProfileSolverTests(BaseDispatchTest):
    """docs/SCENARIO-PROFILES.md: the greedy solver matches a profile per
    cluster, merges its overrides onto the plan's strategy, and applies its
    fallback action when the cluster cannot be placed."""

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

    def test_a_matched_profile_is_recorded_on_applied_stops(self):
        profile = ScenarioProfile.objects.create(name="Any Load")   # no criteria - matches everything
        pv = self._plan_vehicle()
        task = self._task(self.dropA, 2000)
        routes, outsourced, skipped = greedy.solve([pv], [task], scenario_profiles=[profile])
        self.assertEqual(outsourced, [])
        route = routes[0]
        self.assertTrue(route.used)
        drop_stop = next(s for s in route.stops if s["stop_type"] == "drop")
        self.assertEqual(drop_stop["matched_profile"], profile)

    def test_no_scenario_profiles_leaves_clusters_unmatched(self):
        pv = self._plan_vehicle()
        task = self._task(self.dropA, 2000)
        routes, outsourced, skipped = greedy.solve([pv], [task])
        self.assertEqual(outsourced, [])
        drop_stop = next(s for s in routes[0].stops if s["stop_type"] == "drop")
        self.assertIsNone(drop_stop["matched_profile"])

    def test_hold_fallback_action_holds_instead_of_outsourcing(self):
        profile = ScenarioProfile.objects.create(name="Reefer", match_temperature_classes=["frozen"], fallback_action="hold")
        pv = self._plan_vehicle(temperature_class="dry")   # cannot take frozen cargo
        task = self._task(self.dropA, 500, temperature_class="frozen", outsource_estimate=Decimal("0"))
        routes, outsourced, skipped = greedy.solve([pv], [task], scenario_profiles=[profile])
        self.assertEqual(len(outsourced), 1)
        cluster, reason = outsourced[0]
        self.assertEqual(cluster.disposition, "held")
        self.assertEqual(cluster.matched_profile, profile)
        self.assertEqual(reason, "temperature class mismatch")

    def test_defer_fallback_action_defers_instead_of_outsourcing(self):
        profile = ScenarioProfile.objects.create(name="Long Haul", match_min_distance_km=Decimal("1"), fallback_action="defer")
        pv = self._plan_vehicle(capacity_kg=100)   # too small for the task below
        task = self._task(self.dropA, 2000, outsource_estimate=Decimal("500"))
        routes, outsourced, skipped = greedy.solve([pv], [task], scenario_profiles=[profile])
        self.assertEqual(len(outsourced), 1)
        cluster, reason = outsourced[0]
        self.assertEqual(cluster.disposition, "deferred")

    def test_hold_fallback_is_allowed_even_when_allow_partial_service_is_false(self):
        """A hold is a deliberate, visible stop, not a silently lost load - so
        unlike every other disposition it is let through even when the plan's
        own strategy refuses to drop anything (docs/SCENARIO-PROFILES.md)."""
        from dispatch.strategies import resolve_strategy
        profile = ScenarioProfile.objects.create(name="Reefer", match_temperature_classes=["frozen"], fallback_action="hold")
        pv = self._plan_vehicle(temperature_class="dry")
        task = self._task(self.dropA, 500, temperature_class="frozen", outsource_estimate=Decimal("0"))
        strategy = resolve_strategy({"constraints": {"allow_partial_service": False}})
        routes, outsourced, skipped = greedy.solve([pv], [task], strategy, scenario_profiles=[profile])
        self.assertEqual(len(outsourced), 1)
        self.assertEqual(outsourced[0][0].disposition, "held")

    def test_relax_fallback_retries_under_the_fallback_profile(self):
        fallback = ScenarioProfile.objects.create(name="Reefer Relaxed")   # no overrides - the vehicle fits fine under this one
        primary = ScenarioProfile.objects.create(name="Reefer Strict", priority=5,
                                                 constraint_overrides={"max_route_km": 1},   # infeasible for any real route
                                                 fallback_action="relax", fallback_profile=fallback)
        pv = self._plan_vehicle()   # max_route_km=800
        task = self._task(self.dropA, 2000)
        routes, outsourced, skipped = greedy.solve([pv], [task], scenario_profiles=[primary, fallback])
        self.assertEqual(outsourced, [])
        self.assertTrue(routes[0].used)
        drop_stop = next(s for s in routes[0].stops if s["stop_type"] == "drop")
        self.assertEqual(drop_stop["matched_profile"], fallback)

    def test_relax_fallback_without_a_configured_fallback_profile_outsources(self):
        profile = ScenarioProfile.objects.create(name="Reefer Strict", constraint_overrides={"max_route_km": 1},
                                                 fallback_action="relax")   # no fallback_profile set
        pv = self._plan_vehicle()
        task = self._task(self.dropA, 2000, outsource_estimate=Decimal("1"))
        routes, outsourced, skipped = greedy.solve([pv], [task], scenario_profiles=[profile])
        self.assertEqual(len(outsourced), 1)
        self.assertEqual(outsourced[0][0].disposition, "outsourced")


class ScenarioProfileEngineTests(BaseDispatchTest):
    """docs/SCENARIO-PROFILES.md: `solve_plan` feeds every active scenario
    profile into the greedy solver and persists what happened on the task and
    the plan summary."""

    def _solved_plan_with_profile(self, order_number, weight_kg=2000, **profile_overrides):
        if profile_overrides:
            ScenarioProfile.objects.create(**profile_overrides)
        order = self.make_order(order_number, self.dropA, weight_kg)
        inputs.collect_tasks(self.plan)
        inputs.build_plan_vehicles(self.plan)
        self.plan.status = "ready"
        self.plan.save()
        solve_plan(self.plan)
        return order

    def test_matched_scenario_is_recorded_on_a_planned_task(self):
        order = self._solved_plan_with_profile("ORD-SP-1", name="Any Load")   # matches everything
        task = DispatchTask.objects.get(order=order)
        self.assertEqual(task.status, "planned")
        self.assertEqual(task.matched_scenario.name, "Any Load")

    def test_deferred_disposition_does_not_create_a_hire_requirement(self):
        self.vehicle.capacity_kg = 100   # too small - forces a fallback
        self.vehicle.save()
        order = self._solved_plan_with_profile("ORD-SP-2", name="Always Defer", fallback_action="defer")
        task = DispatchTask.objects.get(order=order)
        self.assertEqual(task.status, "deferred")
        self.assertEqual(HireRequirement.objects.filter(plan=self.plan).count(), 0)

    def test_held_disposition_does_not_create_a_hire_requirement(self):
        self.vehicle.capacity_kg = 100
        self.vehicle.save()
        order = self._solved_plan_with_profile("ORD-SP-3", name="Always Hold", fallback_action="hold")
        task = DispatchTask.objects.get(order=order)
        self.assertEqual(task.status, "held_for_review")
        self.assertEqual(HireRequirement.objects.filter(plan=self.plan).count(), 0)

    def test_plan_summary_includes_disposition_counts_and_scenario_breakdown(self):
        self._solved_plan_with_profile("ORD-SP-4", name="Any Load")
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.summary["outsourced_count"], 0)
        self.assertEqual(self.plan.summary["deferred_count"], 0)
        self.assertEqual(self.plan.summary["held_for_review_count"], 0)
        self.assertEqual(self.plan.summary["scenario_breakdown"], {"Any Load": 1})

    def test_solver_status_reports_deferred_and_held_counts(self):
        self.vehicle.capacity_kg = 100
        self.vehicle.save()
        self._solved_plan_with_profile("ORD-SP-5", name="Always Hold", fallback_action="hold")
        self.plan.refresh_from_db()
        self.assertIn("1 held for review", self.plan.solver_status)

    def test_inactive_profile_is_never_matched(self):
        order = self._solved_plan_with_profile("ORD-SP-6", name="Disabled", active=False)
        task = DispatchTask.objects.get(order=order)
        self.assertIsNone(task.matched_scenario)


class ScenarioProfileApiTests(BaseDispatchTest):
    """docs/SCENARIO-PROFILES.md: CRUD, validation that reuses the strategy
    resolver's own key/type checks, and the preview action a dispatcher uses
    to sanity-check a profile's criteria against real demand."""

    def test_create_scenario_profile_via_api(self):
        response = self.client.post("/api/v1/dispatch/scenario-profiles/", {
            "name": "API Milk Run", "scenario_type": "milk_run", "match_min_drops": 2,
            "base_strategy": "max_utilisation", "weight_overrides": {"utilisation_bonus": 50.0},
            "fallback_action": "outsource",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(ScenarioProfile.objects.filter(name="API Milk Run").exists())

    def test_create_rejects_an_unknown_base_strategy(self):
        response = self.client.post("/api/v1/dispatch/scenario-profiles/", {
            "name": "Bad Strategy", "base_strategy": "warp_speed",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("base_strategy", response.data)

    def test_create_rejects_an_unknown_weight_override_key(self):
        response = self.client.post("/api/v1/dispatch/scenario-profiles/", {
            "name": "Bad Weight", "weight_overrides": {"not_a_real_weight": 1},
        }, format="json")
        self.assertEqual(response.status_code, 400)

    def test_a_profile_cannot_be_its_own_fallback(self):
        profile = ScenarioProfile.objects.create(name="Self Referencing")
        response = self.client.patch(f"/api/v1/dispatch/scenario-profiles/{profile.id}/", {
            "fallback_action": "relax", "fallback_profile": profile.id,
        }, format="json")
        self.assertEqual(response.status_code, 400)

    def test_preview_action_reports_matching_clusters(self):
        profile = ScenarioProfile.objects.create(name="Preview Target", match_min_drops=2)
        self.make_order("ORD-SP-PREVIEW-1", self.dropA, 1000)
        self.make_order("ORD-SP-PREVIEW-2", self.dropB, 1000)
        inputs.collect_tasks(self.plan)
        response = self.client.get(f"/api/v1/dispatch/scenario-profiles/{profile.id}/preview/?plan={self.plan.id}")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["matched_clusters"], 1)
        self.assertEqual(response.data["matched_tasks"], 2)

    def test_preview_requires_a_plan_query_param(self):
        profile = ScenarioProfile.objects.create(name="No Plan Given")
        response = self.client.get(f"/api/v1/dispatch/scenario-profiles/{profile.id}/preview/")
        self.assertEqual(response.status_code, 400)


class ScenarioProfileSeedCommandTests(TestCase):
    """docs/SCENARIO-PROFILES.md: the four starter profiles a dispatcher can
    tune from the Scenario Profiles screen, seeded idempotently."""

    def test_seed_command_is_idempotent_and_creates_four_profiles(self):
        call_command("seed_scenario_profiles")
        self.assertEqual(ScenarioProfile.objects.count(), 4)
        self.assertEqual(set(ScenarioProfile.objects.values_list("name", flat=True)),
                         {"Milk Run", "Long Haul", "Reefer", "Local Delivery"})

        call_command("seed_scenario_profiles")
        self.assertEqual(ScenarioProfile.objects.count(), 4)
