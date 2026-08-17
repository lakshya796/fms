"""Turn live fleet data into the solver's inputs: `PlanVehicle` snapshots and
`DispatchTask` rows, plus the readiness check that gates a solve.

Scope note (see docs/DISPATCH-PLANNING.md §6.7): this pass builds `PlanVehicle`
rows from own, attached and leased `fleet.Vehicle` records. Genuinely spot
capacity - a vendor with no truck yet on file - is not pre-loaded as a vehicle;
it is priced per task as `outsource_estimate` and the solver's disjunction
mechanism decides whether to use it. Pre-arranged vendor trucks already on
`fleet.Vehicle` (ownership="attached") participate as real routes with a cost
from their own hire history.

**Faithful collection** (docs/DISPATCH-PLANNER-V2.md §4): `collect_tasks` used
to hardcode `temperature_class="dry"`, `task_type="ftl"`, `priority="normal"`
and no pickup window on every task, regardless of what the order or indent
actually recorded - so a frozen consignment was planned onto a dry truck and a
`must_go` load could be outsourced away. It now reads `temperature_class` and
`priority` off the record, infers `task_type` from `Order.order_type`, and
derives a pickup window from the pickup `Place.loading_hours` free-text field
(already on the schema, e.g. "09:00-18:00") for whatever date the load is due.
"""
import re
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from fleet.models import ComplianceDocument, Driver, Indent, Order, Vehicle, money

from . import costing, matrix

EXCLUDED_STATUSES = {"under_maintenance", "breakdown", "inactive", "driver_unavailable"}
FREE_STATUSES = ("available", "idle")

MUST_GO_PRIORITY_VALUES = {"urgent", "critical", "must_go", "high"}
DEFERRABLE_PRIORITY_VALUES = {"low", "deferrable"}
_LOADING_HOURS_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def _priority_from(obj):
    """Map the record's own free-text `priority` (and, failing that, whether
    it is already overdue) onto the solver's must_go/normal/deferrable scale."""
    raw = (getattr(obj, "priority", "") or "").strip().lower()
    if raw in MUST_GO_PRIORITY_VALUES:
        return "must_go"
    if raw in DEFERRABLE_PRIORITY_VALUES:
        return "deferrable"
    deadline = getattr(obj, "required_at", None) or getattr(obj, "scheduled_at", None)
    if deadline and deadline < timezone.now():
        return "must_go"    # already overdue - cannot be deferred further
    return "normal"


def _pickup_window(place, around):
    """Parse `Place.loading_hours` ("HH:MM-HH:MM") against the date `around`
    falls on, so a plant's stated loading hours become a real constraint
    instead of being silently ignored. Returns (start, end) or (None, None)
    when the field is blank or does not parse - operator-typed free text
    degrades to "no window" rather than raising."""
    if not place.loading_hours:
        return None, None
    match = _LOADING_HOURS_RE.match(place.loading_hours)
    if not match:
        return None, None
    day = timezone.localtime(around).date() if around else timezone.localdate()
    start_h, start_m, end_h, end_m = (int(group) for group in match.groups())
    tzinfo = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time(start_h, start_m)), tzinfo)
    end = timezone.make_aware(datetime.combine(day, time(end_h, end_m)), tzinfo)
    if end <= start:
        end += timedelta(days=1)   # an overnight window, e.g. "22:00-05:00"
    return start, end


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
    """Snapshots eligible vehicles as `PlanVehicle` rows, each with a driver
    already assigned where one is available - a route the solver builds is
    otherwise driverless by construction and blocks at commit for a reason
    that is not really a reason (docs/DISPATCH-PLANNER-V2.md §8.3).

    Scope, stated plainly: this checks status and licence expiry, and hands
    out drivers greedily (first free one not already used in this plan) - it
    does not rank by proximity to the vehicle's start position, and there is
    no duty-hours-remaining field on `fleet.Driver` for this pass to check.
    """
    from ..models import PlanVehicle
    costing.reset_cache()
    today = timezone.localdate()
    PlanVehicle.objects.filter(plan=plan).delete()

    vehicles = Vehicle.objects.filter(ownership__in=["own", "attached", "leased"]) \
                              .select_related("vendor", "home_place", "current_place")
    expired_vehicle_ids = set(ComplianceDocument.objects.filter(
        vehicle__in=vehicles, expiry_date__isnull=False, expiry_date__lt=today).values_list("vehicle_id", flat=True))

    available_drivers = list(Driver.objects.filter(status="available")
                                            .filter(Q(licence_expiry__isnull=True) | Q(licence_expiry__gte=today)))
    unassigned_driver_ids = {driver.pk: driver for driver in available_drivers}

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

        driver = None
        if not excluded and unassigned_driver_ids:
            driver_id = next(iter(unassigned_driver_ids))
            driver = unassigned_driver_ids.pop(driver_id)

        created.append(PlanVehicle.objects.create(
            plan=plan, vehicle=vehicle, driver=driver, source=vehicle.ownership, vendor=vehicle.vendor,
            start_latitude=lat, start_longitude=lng, start_place=vehicle.current_place or vehicle.home_place,
            position_stale=stale, available_from=available_from, must_return_to=vehicle.home_place,
            capacity_kg=vehicle.capacity_kg, capacity_cbm=vehicle.volume_cbm, temperature_class=vehicle.temperature_class,
            cost_per_km=basis["cost_per_km"], cost_per_hour=basis["cost_per_hour"], fixed_cost=basis["fixed_cost"],
            excluded=excluded, exclusion_reason=reason))
    return created


SPOT_SLOT_CAPACITY_KG = Decimal("18000")
SPOT_SLOT_CAPACITY_CBM = Decimal("60")


def build_spot_slot_vehicles(plan):
    """One virtual, routable `PlanVehicle` (`source="spot_slot"`, no real
    `fleet.Vehicle` behind it) per active `VendorLaneRate` covering a pickup
    city this plan actually has demand from.

    Before this, spot capacity was only ever priced as a per-task
    `outsource_estimate` - a hired truck could be *bought*, never *routed*,
    so it could not carry a multi-drop the way an own vehicle can. A
    spot-slot candidate competes in the same clustering pass as everything
    else; it commits like any other route once a real vendor truck is
    actually booked against it (`CarrierOfferViewSet.accept`), and is
    blocked at commit until then, the same as a route with no vehicle
    assigned at all. See docs/DISPATCH-PLANNER-V2.md §7.3.
    """
    from fleet.models import Place, VendorLaneRate

    from ..models import DispatchTask, PlanVehicle

    PlanVehicle.objects.filter(plan=plan, source="spot_slot").delete()

    pickup_cities = set(DispatchTask.objects.filter(plan=plan).exclude(pickup__city="")
                                            .values_list("pickup__city", flat=True))
    if not pickup_cities:
        return []

    today = timezone.localdate()
    rates = VendorLaneRate.objects.filter(origin_city__in=pickup_cities, active=True) \
                                  .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=today)) \
                                  .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=today)) \
                                  .select_related("vendor")

    created = []
    for rate in rates:
        origin_place = Place.objects.filter(city__iexact=rate.origin_city, latitude__isnull=False).first()
        if origin_place is None:
            continue
        cost_per_km = rate.rate if rate.rate_basis == "km" else Decimal("0")
        fixed_cost = rate.rate if rate.rate_basis in ("trip", "day") else Decimal("0")
        created.append(PlanVehicle.objects.create(
            plan=plan, vehicle=None, driver=None, source="spot_slot", vendor=rate.vendor,
            start_latitude=origin_place.latitude, start_longitude=origin_place.longitude, start_place=origin_place,
            available_from=timezone.now(), capacity_kg=SPOT_SLOT_CAPACITY_KG, capacity_cbm=SPOT_SLOT_CAPACITY_CBM,
            temperature_class=rate.temperature_class, cost_per_km=cost_per_km, fixed_cost=fixed_cost,
            max_stops=20, max_route_km=1500, max_duty_minutes=900))
    return created


def collect_tasks(plan, filters=None):
    """`filters` narrows the demand universe collected onto the plan - see
    docs/DISPATCH-PLANNER-V2.md §4.3. Recognised keys: `customers`,
    `pickup_places`, `temperature_class`, `scheduled_from`, `scheduled_to`
    (ISO datetimes), `order_ids` (an explicit hand-picked set - overrides
    every other key and excludes indents entirely) and `include_indents`
    (default True). Whatever was passed is stored on `plan.collection_filters`
    so a re-collect is repeatable and the plan records what it was planning
    over."""
    from ..models import DispatchTask
    costing.reset_cache()
    DispatchTask.objects.filter(plan=plan).delete()

    filters = filters or {}
    plan.collection_filters = filters
    plan.save(update_fields=["collection_filters", "updated_at"])

    indents = Indent.objects.filter(status="open").select_related("pickup", "dropoff", "branch")
    orders = Order.objects.filter(status__in=["created", "assigned"], trip__isnull=True).select_related("pickup", "dropoff", "branch")
    if plan.branch_id:
        indents = indents.filter(branch_id=plan.branch_id)
        orders = orders.filter(branch_id=plan.branch_id)

    order_ids = filters.get("order_ids")
    if order_ids:
        orders = orders.filter(pk__in=order_ids)
        indents = indents.none()
    else:
        customers = filters.get("customers")
        if customers:
            indents = indents.filter(customer_id__in=customers)
            orders = orders.filter(customer_id__in=customers)
        pickup_places = filters.get("pickup_places")
        if pickup_places:
            indents = indents.filter(pickup_id__in=pickup_places)
            orders = orders.filter(pickup_id__in=pickup_places)
        temperature_class = filters.get("temperature_class")
        if temperature_class:
            indents = indents.filter(temperature_class=temperature_class)
            orders = orders.filter(temperature_class=temperature_class)
        scheduled_from = filters.get("scheduled_from")
        if scheduled_from:
            indents = indents.filter(required_at__gte=scheduled_from)
            orders = orders.filter(scheduled_at__gte=scheduled_from)
        scheduled_to = filters.get("scheduled_to")
        if scheduled_to:
            indents = indents.filter(required_at__lte=scheduled_to)
            orders = orders.filter(scheduled_at__lte=scheduled_to)
        if filters.get("include_indents") is False:
            indents = indents.none()

    created = []
    for kind, queryset in (("indent", indents), ("order", orders)):
        for obj in queryset:
            pickup, dropoff = obj.pickup, obj.dropoff
            distance_km = None
            if pickup.latitude is not None and dropoff.latitude is not None:
                distance_km, _ = matrix.distance_and_duration(
                    (pickup.latitude, pickup.longitude), (dropoff.latitude, dropoff.longitude))
            revenue = money(getattr(obj, "total_amount", None) or getattr(obj, "expected_rate", None) or 0)
            # Lane-level pricing (docs/DISPATCH-PLANNER-V2.md §7.1) rather than
            # one flat national rate for every lane, vehicle type and season.
            vehicle_type = (getattr(obj, "vehicle_type", "") or "")
            rate_per_km, confidence = costing.spot_rate_for_lane(
                pickup, dropoff, distance_km=distance_km, vehicle_type=vehicle_type, temperature_class=obj.temperature_class)
            outsource = money((distance_km or Decimal("0")) * rate_per_km)
            deadline = getattr(obj, "required_at", None) or getattr(obj, "scheduled_at", None)
            pickup_start, pickup_end = _pickup_window(pickup, deadline)
            task_type = "ftl" if getattr(obj, "order_type", "ftl") == "ftl" else "multi_drop_leg"
            created.append(DispatchTask.objects.create(
                plan=plan, order=obj if kind == "order" else None, indent=obj if kind == "indent" else None,
                task_type=task_type, pickup=pickup, dropoff=dropoff, weight_kg=obj.weight_kg or 0,
                volume_cbm=getattr(obj, "volume_cbm", 0) or 0, temperature_class=obj.temperature_class,
                temp_set_point_c=obj.temp_set_point_c, pickup_window_start=pickup_start, pickup_window_end=pickup_end,
                revenue_estimate=revenue, outsource_estimate=outsource, outsource_confidence=confidence,
                drop_window_end=deadline, priority=_priority_from(obj)))
    return created
