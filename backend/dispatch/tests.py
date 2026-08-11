"""Tests for the CVRP dispatch planning module.

Two layers, matching `fleet/tests.py`'s convention: solver-level invariant tests
that do not touch the database beyond fixtures (capacity, temperature,
own-vs-outsource), and API-level tests for the collect -> solve -> commit round
trip that actually lands orders on trips.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from fleet.models import Customer, Driver, Order, Place, ServiceArea, Trip, TripExpense, Vehicle
from iam.models import Role, UserProfile

from .models import DispatchPlan, DispatchTask, HireRequirement, PlannedRoute, PlanVehicle
from .solver import greedy, inputs
from .solver.engine import solve_plan


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
