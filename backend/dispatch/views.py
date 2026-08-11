from uuid import uuid4

from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from fleet.models import Order as FleetOrder
from fleet.models import Trip, VehicleHire, money, set_vehicle_status
from iam.filtering import apply_filters
from iam.messaging import send_email
from iam.permissions import HasModulePermission

from .models import DispatchPlan, DispatchTask, HireRequirement, PlanEvent, PlannedRoute, PlanVehicle
from .serializers import (DispatchPlanDetailSerializer, DispatchPlanSerializer, DispatchTaskSerializer,
                          HireRequirementSerializer, PlanEventSerializer, PlannedRouteSerializer, PlanVehicleSerializer)
from .solver import inputs
from .solver.engine import solve_plan


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
        snapshot the eligible fleet as `PlanVehicle` offers."""
        plan = self.get_object()
        if plan.status in ("committed", "superseded"):
            raise ValidationError(f"A {plan.status} plan cannot collect new demand.")
        tasks = inputs.collect_tasks(plan)
        vehicles = inputs.build_plan_vehicles(plan)
        plan.status = "ready"
        plan.save(update_fields=["status", "updated_at"])
        plan.log("collected", f"{len(tasks)} task(s), {len(vehicles)} vehicle(s)")
        return Response({"plan": DispatchPlanSerializer(plan).data, "task_count": len(tasks), "vehicle_count": len(vehicles)})

    @action(detail=True, methods=["post"])
    def solve(self, request, pk=None):
        plan = self.get_object()
        if plan.status not in ("ready", "solved", "failed"):
            raise ValidationError("Collect demand before solving, or this plan is already committed.")
        try:
            solve_plan(plan)
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

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def commit(self, request, pk=None):
        """Land the plan: one Trip per route, orders (and indents converted to
        orders) linked to it, vehicles moved to `allocated`. Idempotent per route -
        a route that already has a `committed_trip` is left untouched, so this can
        be called again for whichever routes were blocked the first time.
        """
        _require_commit_permission(request)
        plan = self.get_object()
        if plan.status == "committed":
            return Response({"detail": "Already committed.", "plan": DispatchPlanSerializer(plan).data})
        if plan.status != "solved":
            raise ValidationError("Solve the plan before committing it.")

        committed_routes, blocked_routes = [], []
        routes = plan.routes.select_related("plan_vehicle__vehicle", "plan_vehicle__driver") \
                            .prefetch_related("stops__task__order", "stops__task__indent")
        for route in routes:
            if route.committed_trip_id:
                continue
            plan_vehicle = route.plan_vehicle
            if not plan_vehicle.vehicle_id or not plan_vehicle.driver_id:
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

            trip = Trip.objects.create(
                number="TRP-" + timezone.now().strftime("%y%m%d") + uuid4().hex[:6].upper(),
                vehicle=plan_vehicle.vehicle, driver=plan_vehicle.driver,
                origin=stops[0].place.city or stops[0].place.name, destination=stops[-1].place.city or stops[-1].place.name,
                planned_departure=stops[0].planned_arrival or timezone.now(), status="planned")

            for stop, order in orders:
                order.vehicle = plan_vehicle.vehicle
                order.driver = plan_vehicle.driver
                order.trip = trip
                order.status = "assigned"
                order.save(update_fields=["vehicle", "driver", "trip", "status", "updated_at"])
                order.log("assigned", "DISPATCH_PLAN_COMMITTED", f"Planned on {plan.code}", city=stop.place.city)
                stop.task.status = "committed"
                stop.task.save(update_fields=["status", "updated_at"])

            set_vehicle_status(plan_vehicle.vehicle, "allocated", trip=trip, reason=f"Dispatch plan {plan.code}")
            route.committed_trip = trip
            route.locked = True
            route.save(update_fields=["committed_trip", "locked", "updated_at"])
            plan.log("route_committed", f"Route #{route.sequence} -> {trip.number}")
            committed_routes.append(route.id)

        if committed_routes and not blocked_routes:
            plan.status = "committed"
        plan.committed_at = timezone.now()
        plan.committed_by = request.user.get_username()
        plan.save(update_fields=["status", "committed_at", "committed_by", "updated_at"])
        plan.log("committed", f"{len(committed_routes)} route(s) committed, {len(blocked_routes)} blocked")
        return Response({"plan": DispatchPlanSerializer(plan).data,
                         "committed_routes": committed_routes, "blocked_routes": blocked_routes})


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
    def request_quotes(self, request, pk=None):
        """Fan out an RFQ to vendors through the existing outbox, so every message
        is recorded and resendable rather than a phone call nobody logs."""
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
