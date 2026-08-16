from uuid import uuid4

from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from fleet.models import Order as FleetOrder
from fleet.models import Trip, VehicleHire, money, set_vehicle_status
from iam.filtering import apply_filters
from iam.messaging import send_email
from iam.permissions import HasModulePermission

from .models import CarrierOffer, DispatchPlan, DispatchTask, HireRequirement, PlanEvent, PlannedRoute, PlannedStop, PlanVehicle
from .serializers import (CarrierOfferSerializer, DispatchPlanDetailSerializer, DispatchPlanSerializer,
                          DispatchTaskSerializer, HireRequirementSerializer, PlanEventSerializer,
                          PlannedRouteSerializer, PlannedStopSerializer, PlanVehicleSerializer)
from .solver import inputs, tracking
from .solver.engine import solve_plan
from .solver.replan import replan as replan_plan
from .strategies import StrategyError, resolve_strategy, strategy_catalogue


def _require_commit_permission(request):
    """`HasModulePermission` only distinguishes read/write by HTTP method, but
    committing a plan (moving real vehicles and orders) is a higher bar than
    planning one - so the one action that needs it checks explicitly."""
    profile = getattr(request.user, "profile", None)
    if request.user.is_superuser or profile is None or profile.role is None:
        return
    if not profile.allows("dispatch.commit"):
        raise ValidationError("Your role does not allow committing a dispatch plan.")


class DispatchViewSet(viewsets.ModelViewSet):
    permission_classes = [HasModulePermission]
    required_permission = "dispatch.view"
    required_write_permission = "dispatch.plan"
    filter_fields: list = []
    search_fields: list = []

    def get_queryset(self):
        return apply_filters(super().get_queryset(), self.request.query_params, self.filter_fields, self.search_fields)


class DispatchPlanViewSet(DispatchViewSet):
    queryset = DispatchPlan.objects.select_related("branch").all()
    serializer_class = DispatchPlanSerializer
    filter_fields = ["status", "branch", "plan_date"]
    search_fields = ["code"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return DispatchPlanDetailSerializer
        return DispatchPlanSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "retrieve":
            queryset = queryset.prefetch_related(
                "routes__stops__task__order", "routes__stops__task__indent", "routes__plan_vehicle__vehicle",
                "tasks", "hire_requirements", "events", "plan_vehicles")
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user.get_username())

    @action(detail=True, methods=["get"])
    def readiness(self, request, pk=None):
        plan = self.get_object()
        return Response(inputs.readiness(plan))

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def collect(self, request, pk=None):
        """Pull open indents and un-trip'd orders into this plan's task list, and
        snapshot the eligible fleet as `PlanVehicle` offers. The body may narrow
        what gets pulled in - `customers`, `pickup_places`, `temperature_class`,
        `scheduled_from`/`scheduled_to`, `order_ids` or `include_indents` - see
        `solver.inputs.collect_tasks` and docs/DISPATCH-PLANNER-V2.md §4.3."""
        plan = self.get_object()
        if plan.status in ("committed", "superseded"):
            raise ValidationError(f"A {plan.status} plan cannot collect new demand.")
        tasks = inputs.collect_tasks(plan, filters=request.data)
        vehicles = inputs.build_plan_vehicles(plan)
        plan.status = "ready"
        plan.save(update_fields=["status", "updated_at"])
        plan.log("collected", f"{len(tasks)} task(s), {len(vehicles)} vehicle(s)")
        return Response({"plan": DispatchPlanSerializer(plan).data, "task_count": len(tasks), "vehicle_count": len(vehicles)})

    @action(detail=True, methods=["post"])
    def solve(self, request, pk=None):
        """Body may carry `strategy` (a preset name), `weights` and
        `constraints` overrides (see strategies.py) - all optional, defaulting
        to the "balanced" preset. See docs/DISPATCH-PLANNER-V2.md §3.3."""
        plan = self.get_object()
        if plan.status not in ("ready", "solved", "failed"):
            raise ValidationError("Collect demand before solving, or this plan is already committed.")
        try:
            strategy = resolve_strategy(request.data)
        except StrategyError as error:
            raise ValidationError(str(error)) from error
        try:
            solve_plan(plan, strategy)
        except Exception as error:  # noqa: BLE001 - surfaced to the dispatcher, not swallowed
            plan.status = "failed"
            plan.solver_status = str(error)
            plan.save(update_fields=["status", "solver_status", "updated_at"])
            raise ValidationError(f"Solve failed: {error}") from error
        plan.refresh_from_db()
        return Response(DispatchPlanDetailSerializer(plan).data)

    @action(detail=True, methods=["get"])
    def explain(self, request, pk=None):
        """Why a task landed where it did (or why it was outsourced)."""
        plan = self.get_object()
        task_id = request.query_params.get("task")
        if not task_id:
            raise ValidationError("Provide ?task=<id>.")
        task = DispatchTask.objects.select_related("pickup", "dropoff").get(pk=task_id, plan=plan)
        stop = task.stops.select_related("route__plan_vehicle__vehicle").first()
        if stop:
            route = stop.route
            return Response({
                "task": DispatchTaskSerializer(task).data, "outcome": "planned",
                "vehicle": getattr(route.plan_vehicle.vehicle, "registration_number", None),
                "route": route.id, "planned_arrival": stop.planned_arrival, "planned_departure": stop.planned_departure,
            })
        return Response({"task": DispatchTaskSerializer(task).data, "outcome": task.status, "reason": task.drop_reason})

    @action(detail=True, methods=["get"])
    def kpis(self, request, pk=None):
        return Response(self.get_object().summary)

    @action(detail=True, methods=["get"], url_path="replan-status")
    def replan_status(self, request, pk=None):
        """Which committed routes are running behind - the signal a dispatcher
        acts on before pressing `replan`, not something replan checks itself."""
        plan = self.get_object()
        threshold = int(request.query_params.get("threshold_minutes", 60))
        return Response({"behind": tracking.routes_needing_replan(plan, threshold_minutes=threshold)})

    @action(detail=True, methods=["post"])
    def replan(self, request, pk=None):
        """Build a child plan for whatever has not yet been picked up (see
        `solver.replan` for exactly what that means) and solve it - never
        commits automatically, same as every other solve."""
        plan = self.get_object()
        try:
            child = replan_plan(plan, created_by=request.user.get_username())
        except ValueError as error:
            raise ValidationError(str(error)) from error
        return Response(DispatchPlanDetailSerializer(child).data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def commit(self, request, pk=None):
        """Land the plan: one Trip per route, orders (and indents converted to
        orders) linked to it, vehicles moved to `allocated`. Idempotent per route -
        a route already committed is left untouched, so this can be called again
        for whichever routes were blocked the first time.

        A route whose vehicle is attached or outside-sourced (vendor's own driver,
        not one of ours) commits without a driver and without a `Trip`: the order
        is linked straight to the vehicle and vendor and a single `VehicleHire`
        covers the whole route, mirroring how `HireRequirementViewSet.award`
        already handles a vendor-driver-only hire.
        """
        _require_commit_permission(request)
        plan = self.get_object()
        if plan.status == "committed":
            return Response({"detail": "Already committed.", "plan": DispatchPlanSerializer(plan).data})
        if plan.status != "solved":
            raise ValidationError("Solve the plan before committing it.")

        committed_routes, blocked_routes = [], []
        routes = plan.routes.select_related("plan_vehicle__vehicle", "plan_vehicle__driver", "plan_vehicle__vendor") \
                            .prefetch_related("stops__task__order", "stops__task__indent")
        for route in routes:
            if _route_already_committed(route):
                continue
            plan_vehicle = route.plan_vehicle
            if not plan_vehicle.vehicle_id:
                blocked_routes.append({"route": route.id, "reason": "No vehicle assigned to this route."})
                continue

            vendor_driven = plan_vehicle.source == "hired" and plan_vehicle.vehicle.ownership in ("outside", "attached")
            if not plan_vehicle.driver_id and not vendor_driven:
                blocked_routes.append({"route": route.id, "reason": "Vehicle and driver must both be set before commit."})
                continue

            stops = list(route.stops.order_by("sequence"))
            orders = []
            for stop in stops:
                task = stop.task
                if task is None:
                    continue
                order = task.order
                if order is None and task.indent_id:
                    order = _convert_indent(task.indent, plan_vehicle)
                    task.order = order
                    task.save(update_fields=["order", "updated_at"])
                if order is not None:
                    orders.append((stop, order))
            if not orders:
                blocked_routes.append({"route": route.id, "reason": "No orders on this route to commit."})
                continue

            needs_trip = plan_vehicle.driver_id is not None
            trip = None
            if needs_trip:
                trip = Trip.objects.create(
                    number="TRP-" + timezone.now().strftime("%y%m%d") + uuid4().hex[:6].upper(),
                    vehicle=plan_vehicle.vehicle, driver=plan_vehicle.driver,
                    origin=stops[0].place.city or stops[0].place.name, destination=stops[-1].place.city or stops[-1].place.name,
                    planned_departure=stops[0].planned_arrival or timezone.now(), status="planned")

            for stop, order in orders:
                order.vehicle = plan_vehicle.vehicle
                if plan_vehicle.driver_id:
                    order.driver = plan_vehicle.driver
                if plan_vehicle.vendor_id:
                    order.vendor = plan_vehicle.vendor
                order.trip = trip
                order.status = "assigned"
                order.save(update_fields=["vehicle", "driver", "vendor", "trip", "status", "updated_at"])
                order.log("assigned", "DISPATCH_PLAN_COMMITTED", f"Planned on {plan.code}", city=stop.place.city)
                stop.task.status = "committed"
                stop.task.save(update_fields=["status", "updated_at"])

            if vendor_driven and plan_vehicle.vendor_id:
                # One commercial hire covers the whole route, the same way a
                # consolidated Trip's freight now sums every order on it (§3.1).
                VehicleHire.objects.create(
                    order=orders[0][1], trip=trip, vendor_id=plan_vehicle.vendor_id, hire_type="spot",
                    outside_vehicle_number=plan_vehicle.vehicle.registration_number,
                    outside_vehicle_type=plan_vehicle.vehicle.vehicle_type, outside_capacity_kg=plan_vehicle.capacity_kg,
                    agreed_rate=money(route.estimated_cost), rate_basis="trip", status="confirmed")

            set_vehicle_status(plan_vehicle.vehicle, "allocated", trip=trip, reason=f"Dispatch plan {plan.code}")
            route.committed_trip = trip
            route.locked = True
            route.save(update_fields=["committed_trip", "locked", "updated_at"])
            plan.log("route_committed", f"Route #{route.sequence} -> {trip.number if trip else 'vendor hire, no trip'}")
            committed_routes.append(route.id)

        if committed_routes and not blocked_routes:
            plan.status = "committed"
        plan.committed_at = timezone.now()
        plan.committed_by = request.user.get_username()
        plan.save(update_fields=["status", "committed_at", "committed_by", "updated_at"])
        plan.log("committed", f"{len(committed_routes)} route(s) committed, {len(blocked_routes)} blocked")
        return Response({"plan": DispatchPlanSerializer(plan).data,
                         "committed_routes": committed_routes, "blocked_routes": blocked_routes})


def _route_already_committed(route):
    """A route committed without a Trip (vendor-driven, see `commit`) has no
    `committed_trip` to check, so idempotency instead looks at whether its own
    tasks already made it to `committed` - set once, in the same transaction,
    for every task on a route."""
    if route.committed_trip_id:
        return True
    first_task_stop = next((s for s in route.stops.all() if s.task_id), None)
    return bool(first_task_stop and first_task_stop.task.status == "committed")


def _convert_indent(indent, plan_vehicle):
    """The same conversion `IndentViewSet.convert` performs, done inline at
    commit time for an indent the plan allocated a vehicle and driver to."""
    indent.vehicle = plan_vehicle.vehicle
    indent.driver = plan_vehicle.driver
    indent.status = "allocated"
    indent.save(update_fields=["vehicle", "driver", "status", "updated_at"])
    order = FleetOrder.objects.create(
        number="ORD-" + timezone.now().strftime("%y%m%d") + uuid4().hex[:6].upper(),
        customer=indent.customer, branch=indent.branch, pickup=indent.pickup, dropoff=indent.dropoff,
        service_rate=indent.service_rate, vehicle=plan_vehicle.vehicle, driver=plan_vehicle.driver,
        payload_description=indent.material, weight_kg=indent.weight_kg, scheduled_at=indent.required_at, status="assigned")
    if order.service_rate:
        order.price_from_rate_card()
    indent.order = order
    indent.status = "converted"
    indent.save(update_fields=["order", "status", "updated_at"])
    return order


class PlannedRouteViewSet(DispatchViewSet):
    queryset = PlannedRoute.objects.select_related("plan", "plan_vehicle__vehicle", "plan_vehicle__driver").prefetch_related("stops").all()
    serializer_class = PlannedRouteSerializer
    filter_fields = ["plan", "feasible", "locked"]

    @action(detail=True, methods=["post"])
    def lock(self, request, pk=None):
        route = self.get_object()
        route.locked = not request.data.get("unlock", False)
        route.save(update_fields=["locked", "updated_at"])
        return Response(self.get_serializer(route).data)

    @action(detail=True, methods=["post"], url_path="assign-driver")
    def assign_driver(self, request, pk=None):
        """A route the solver built with a vehicle but no driver yet - the one
        piece of the commit that stays a human decision (see docs/DISPATCH-PLANNING.md)."""
        route = self.get_object()
        driver_id = request.data.get("driver")
        if not driver_id:
            raise ValidationError("Provide a driver id.")
        from fleet.models import Driver
        route.plan_vehicle.driver = Driver.objects.get(pk=driver_id)
        route.plan_vehicle.save(update_fields=["driver", "updated_at"])
        return Response(self.get_serializer(route).data)


class PlannedStopViewSet(DispatchViewSet):
    """Plan-versus-actual: a driver (or the geofence auto-capture management
    command) confirms arrival and departure at each stop. See
    docs/DISPATCH-PLANNING.md §7.3 - this is the manual/driver-check-in path
    every hired vehicle with no GPS device relies on, and the one every own
    vehicle falls back to whenever `capture_gps_arrivals` has not run yet."""
    queryset = PlannedStop.objects.select_related("route__plan", "place", "task__order").all()
    serializer_class = PlannedStopSerializer
    filter_fields = ["route", "stop_type"]
    http_method_names = ["get", "post", "head", "options"]

    @action(detail=True, methods=["post"])
    def arrive(self, request, pk=None):
        stop = tracking.mark_arrived(self.get_object())
        return Response(self.get_serializer(stop).data)

    @action(detail=True, methods=["post"])
    def depart(self, request, pk=None):
        stop = tracking.mark_departed(self.get_object())
        return Response(self.get_serializer(stop).data)


class PlanVehicleViewSet(DispatchViewSet):
    queryset = PlanVehicle.objects.select_related("plan", "vehicle", "driver", "vendor").all()
    serializer_class = PlanVehicleSerializer
    filter_fields = ["plan", "excluded", "source"]
    http_method_names = ["get", "post", "head", "options"]


class DispatchTaskViewSet(DispatchViewSet):
    queryset = DispatchTask.objects.select_related("plan", "pickup", "dropoff", "order", "indent").all()
    serializer_class = DispatchTaskSerializer
    filter_fields = ["plan", "status", "priority", "temperature_class"]
    http_method_names = ["get", "head", "options"]


class HireRequirementViewSet(DispatchViewSet):
    queryset = HireRequirement.objects.select_related("plan", "pickup", "dropoff").prefetch_related("tasks").all()
    serializer_class = HireRequirementSerializer
    filter_fields = ["plan", "status"]

    @action(detail=True, methods=["post"], url_path="request-quotes")
    @transaction.atomic
    def request_quotes(self, request, pk=None):
        """Fan out an RFQ to vendors through the existing outbox, so every message
        is recorded and resendable rather than a phone call nobody logs - and open
        a `CarrierOffer` per vendor invited, so a phoned-in rate has somewhere to
        be recorded against (`CarrierOfferViewSet.record_quote`)."""
        requirement = self.get_object()
        from fleet.models import Vendor
        vendors = Vendor.objects.filter(vendor_type__in=["transporter", "broker"], status="active", email__gt="")
        sent = []
        for vendor in vendors:
            subject = f"Capacity needed: {requirement.pickup.name} -> {requirement.dropoff.name}"
            body = (f"We need a {requirement.vehicle_type or 'suitable'} vehicle "
                   f"({requirement.temperature_class}) for {requirement.capacity_kg} kg, "
                   f"{requirement.pickup.name} to {requirement.dropoff.name}"
                   + (f", report by {requirement.report_by:%d %b %H:%M}" if requirement.report_by else "") + ".")
            message = send_email(to=vendor.email, subject=subject, body=body, template_key="dispatch_hire_rfq",
                                 reference_type="hire_requirement", reference_id=requirement.pk,
                                 created_by=request.user.get_username())
            CarrierOffer.objects.update_or_create(
                requirement=requirement, vendor=vendor,
                defaults={"message_id": message.pk, "status": "invited",
                         "temperature_class": requirement.temperature_class, "vehicle_type": requirement.vehicle_type})
            sent.append({"vendor": vendor.name, "message_id": message.pk, "status": message.status})
        requirement.status = "quoted" if sent else requirement.status
        requirement.save(update_fields=["status", "updated_at"])
        return Response({"requirement": self.get_serializer(requirement).data, "sent": sent})

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def award(self, request, pk=None):
        """Award this requirement to a vendor at an agreed rate: create the
        VehicleHire and link it to the order(s) it covers, mirroring
        `OrderViewSet.confirm_vehicle` for a plan-sourced hire."""
        _require_commit_permission(request)
        requirement = self.get_object()
        if requirement.status == "awarded":
            raise ValidationError("This requirement has already been awarded.")
        vendor_id = request.data.get("vendor")
        if not vendor_id:
            raise ValidationError("Provide a vendor id.")
        from fleet.models import Vendor
        vendor = Vendor.objects.get(pk=vendor_id)
        tasks = list(requirement.tasks.select_related("order").all())
        orders_with_tasks = [(t.order, t) for t in tasks if t.order_id]
        if not orders_with_tasks:
            raise ValidationError("None of this requirement's tasks have a linked order yet - "
                                  "convert the underlying indent first.")
        order, _ = orders_with_tasks[0]
        hire = VehicleHire.objects.create(
            order=order, vendor=vendor, hire_type=request.data.get("hire_type", "spot"),
            outside_vehicle_number=request.data.get("vehicle_number", ""),
            outside_vehicle_type=requirement.vehicle_type, outside_capacity_kg=requirement.capacity_kg,
            driver_name=request.data.get("driver_name", ""), driver_phone=request.data.get("driver_phone", ""),
            agreed_rate=request.data.get("agreed_rate") or requirement.estimated_cost, rate_basis=request.data.get("rate_basis", "trip"),
            status="confirmed")
        requirement.awarded_hire = hire
        requirement.status = "awarded"
        requirement.save(update_fields=["awarded_hire", "status", "updated_at"])
        for order, task in orders_with_tasks:
            order.vendor = vendor
            order.status = "assigned"
            order.save(update_fields=["vendor", "status", "updated_at"])
            task.status = "committed"
            task.save(update_fields=["status", "updated_at"])
        requirement.plan.log("quote_accepted", f"{vendor.name} awarded for {requirement.pickup.name} -> {requirement.dropoff.name}")
        return Response({"requirement": self.get_serializer(requirement).data, "hire_id": hire.pk})


class CarrierOfferViewSet(DispatchViewSet):
    """A vendor's response to a `HireRequirement` RFQ. There is no vendor
    self-service portal yet, so `record_quote` is dispatch-desk data entry for a
    rate that came in by phone or email - not a webhook from the vendor."""
    queryset = CarrierOffer.objects.select_related("requirement__plan", "requirement__pickup", "requirement__dropoff", "vendor").all()
    serializer_class = CarrierOfferSerializer
    filter_fields = ["requirement", "vendor", "status"]

    @action(detail=True, methods=["post"], url_path="record-quote")
    def record_quote(self, request, pk=None):
        offer = self.get_object()
        if offer.status in ("accepted", "rejected", "expired"):
            raise ValidationError(f"This offer is already {offer.status}.")
        serializer = self.get_serializer(offer, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(status="quoted", responded_at=timezone.now())
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def accept(self, request, pk=None):
        """Award this offer: register the vendor's truck as a real `fleet.Vehicle`,
        add it to the plan as a routable `PlanVehicle`, reopen its requirement's
        tasks and re-solve - a hired truck arriving cheap can pull work back off
        the market and reshape the routes around it (§8.3). Only possible before
        commit; once routes are real trips, a fresh plan covers further capacity.

        The requirement stays `awarded` for audit even though its tasks may land
        somewhere other than this exact vehicle once the re-solve runs - it
        records that sourcing succeeded, not the final routing.
        """
        _require_commit_permission(request)
        offer = self.get_object()
        requirement = offer.requirement
        plan = requirement.plan
        if plan.status == "committed":
            raise ValidationError("This plan is already committed - raise a new plan for further capacity.")
        if offer.status not in ("invited", "quoted"):
            raise ValidationError(f"An offer that is {offer.status} cannot be accepted.")

        from fleet.models import Vehicle
        vehicle, _ = Vehicle.objects.get_or_create(
            registration_number=offer.vehicle_number or f"SPOT-{offer.pk}",
            defaults={"vehicle_type": offer.vehicle_type or requirement.vehicle_type,
                     "capacity_kg": requirement.capacity_kg, "ownership": "outside", "vendor": offer.vendor,
                     "temperature_class": offer.temperature_class or requirement.temperature_class, "status": "available"})

        rate_basis = offer.rate_basis or "trip"
        PlanVehicle.objects.create(
            plan=plan, vehicle=vehicle, source="hired", vendor=offer.vendor,
            start_latitude=requirement.pickup.latitude, start_longitude=requirement.pickup.longitude,
            start_place=requirement.pickup, available_from=timezone.now(),
            capacity_kg=vehicle.capacity_kg, capacity_cbm=vehicle.volume_cbm, temperature_class=vehicle.temperature_class,
            cost_per_km=offer.offered_rate if rate_basis == "km" else 0,
            fixed_cost=offer.offered_rate if rate_basis in ("trip", "day") else 0)

        requirement.tasks.update(status="pending")
        offer.status = "accepted"
        offer.responded_at = timezone.now()
        offer.save(update_fields=["status", "responded_at", "updated_at"])
        CarrierOffer.objects.filter(requirement=requirement).exclude(pk=offer.pk).exclude(status="rejected") \
                            .update(status="rejected", responded_at=timezone.now())
        requirement.status = "awarded"
        requirement.save(update_fields=["status", "updated_at"])
        plan.log("quote_accepted", f"{offer.vendor.name} accepted for {requirement.pickup.name} -> {requirement.dropoff.name}")

        solve_plan(plan)
        plan.refresh_from_db()
        return Response(DispatchPlanDetailSerializer(plan).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def strategies_view(request):
    """The preset catalogue with each one's fully expanded weight vector, so
    the solve panel builds its picker from the server rather than a hardcoded
    copy. See docs/DISPATCH-PLANNER-V2.md §3.3."""
    return Response(strategy_catalogue())
