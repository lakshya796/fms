"""Turn live fleet data into the solver's inputs: `PlanVehicle` snapshots and
`DispatchTask` rows, plus the readiness check that gates a solve.

Scope note (see docs/DISPATCH-PLANNING.md §6.7): this pass builds `PlanVehicle`
rows from own, attached and leased `fleet.Vehicle` records. Genuinely spot
capacity - a vendor with no truck yet on file - is not pre-loaded as a vehicle;
it is priced per task as `outsource_estimate` and the solver's disjunction
mechanism decides whether to use it. Pre-arranged vendor trucks already on
`fleet.Vehicle` (ownership="attached") participate as real routes with a cost
from their own hire history.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from fleet.models import ComplianceDocument, Indent, Order, Vehicle, money

from . import costing, matrix

EXCLUDED_STATUSES = {"under_maintenance", "breakdown", "inactive", "driver_unavailable"}
FREE_STATUSES = ("available", "idle")


def readiness(plan):
    """What would block or degrade a solve, so a dispatcher finds out before
    pressing solve rather than from an empty plan."""
    warnings, blockers = [], []
    vehicles = Vehicle.objects.filter(ownership__in=["own", "attached", "leased"])
    no_capacity = vehicles.filter(capacity_kg=0).count()
    if no_capacity:
        blockers.append(f"{no_capacity} vehicle(s) have no weight capacity on file.")
    no_position = vehicles.filter(current_latitude__isnull=True, home_place__isnull=True).count()
    if no_position:
        warnings.append(f"{no_position} vehicle(s) have neither a live position nor a home place - "
                        "they will be excluded from the plan.")
    indents = Indent.objects.filter(status="open")
    orders = Order.objects.filter(status__in=["created", "assigned"], trip__isnull=True)
    bad_places = 0
    for obj in list(indents) + list(orders):
        if obj.pickup.latitude is None or obj.dropoff.latitude is None:
            bad_places += 1
    if bad_places:
        blockers.append(f"{bad_places} pending indent/order(s) have a pickup or dropoff place with no coordinates.")
    return {"ready": not blockers, "blockers": blockers, "warnings": warnings}


def build_plan_vehicles(plan):
    from ..models import PlanVehicle
    costing.reset_cache()
    today = timezone.localdate()
    PlanVehicle.objects.filter(plan=plan).delete()

    vehicles = Vehicle.objects.filter(ownership__in=["own", "attached", "leased"]) \
                              .select_related("vendor", "home_place", "current_place")
    expired_vehicle_ids = set(ComplianceDocument.objects.filter(
        vehicle__in=vehicles, expiry_date__isnull=False, expiry_date__lt=today).values_list("vehicle_id", flat=True))

    created = []
    for vehicle in vehicles:
        excluded, reason = False, ""
        if vehicle.status in EXCLUDED_STATUSES:
            excluded, reason = True, f"Vehicle status is {vehicle.get_status_display()}"
        elif vehicle.pk in expired_vehicle_ids:
            excluded, reason = True, "A statutory document has expired"

        lat = vehicle.current_latitude
        lng = vehicle.current_longitude
        stale = lat is None
        if lat is None and vehicle.home_place_id:
            lat, lng = vehicle.home_place.latitude, vehicle.home_place.longitude
        if lat is None and not excluded:
            excluded, reason = True, "No live position and no home place on file"

        if vehicle.status in FREE_STATUSES:
            available_from = timezone.now()
        else:
            available_from = vehicle.expected_available_at or (timezone.now() + timedelta(hours=6))

        basis = costing.vehicle_cost_basis(vehicle) if vehicle.ownership == "own" else costing.vendor_cost_basis(vehicle)

        created.append(PlanVehicle.objects.create(
            plan=plan, vehicle=vehicle, source=vehicle.ownership, vendor=vehicle.vendor,
            start_latitude=lat, start_longitude=lng, start_place=vehicle.current_place or vehicle.home_place,
            position_stale=stale, available_from=available_from, must_return_to=vehicle.home_place,
            capacity_kg=vehicle.capacity_kg, capacity_cbm=vehicle.volume_cbm, temperature_class=vehicle.temperature_class,
            cost_per_km=basis["cost_per_km"], cost_per_hour=basis["cost_per_hour"], fixed_cost=basis["fixed_cost"],
            excluded=excluded, exclusion_reason=reason))
    return created


def collect_tasks(plan):
    from ..models import DispatchTask
    costing.reset_cache()
    DispatchTask.objects.filter(plan=plan).delete()

    indents = Indent.objects.filter(status="open").select_related("pickup", "dropoff", "branch")
    orders = Order.objects.filter(status__in=["created", "assigned"], trip__isnull=True).select_related("pickup", "dropoff", "branch")
    if plan.branch_id:
        indents = indents.filter(branch_id=plan.branch_id)
        orders = orders.filter(branch_id=plan.branch_id)

    spot_rate = costing.spot_rate_per_km()
    created = []
    for kind, queryset in (("indent", indents), ("order", orders)):
        for obj in queryset:
            pickup, dropoff = obj.pickup, obj.dropoff
            distance_km = None
            if pickup.latitude is not None and dropoff.latitude is not None:
                distance_km, _ = matrix.distance_and_duration(
                    (pickup.latitude, pickup.longitude), (dropoff.latitude, dropoff.longitude))
            revenue = money(getattr(obj, "total_amount", None) or getattr(obj, "expected_rate", None) or 0)
            outsource = money((distance_km or Decimal("0")) * spot_rate)
            deadline = getattr(obj, "required_at", None) or getattr(obj, "scheduled_at", None)
            created.append(DispatchTask.objects.create(
                plan=plan, order=obj if kind == "order" else None, indent=obj if kind == "indent" else None,
                task_type="ftl", pickup=pickup, dropoff=dropoff, weight_kg=obj.weight_kg or 0,
                volume_cbm=getattr(obj, "volume_cbm", 0) or 0, temperature_class="dry",
                revenue_estimate=revenue, outsource_estimate=outsource,
                drop_window_end=deadline, priority="normal"))
    return created
