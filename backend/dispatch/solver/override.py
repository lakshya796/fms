"""Manual overrides on an already-solved plan: move a task between routes,
reorder one route's stops, or take a task off its route entirely. Every
mutation re-costs whatever route(s) it touches immediately, so the plan's
numbers are never stale after a dispatcher's edit. Pinning a task to a
vehicle (so the *next* solve honours it) is handled by `DispatchTask.pinned_vehicle`
directly and read by `greedy.solve` - there is nothing to recompute for that
one, since nothing routed changes until the plan is solved again.

See docs/DISPATCH-PLANNER-V2.md §8.1.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from fleet.models import money

from . import matrix


def recompute_route(route):
    """Walk `route`'s current stop sequence from the vehicle's start position
    and recompute everything derived from it: distance, dead km, drive time,
    the load curve, cost/revenue/margin, utilisation, and a fresh ETA at each
    stop. This is the one function every override below relies on - a manual
    edit is only honest if the numbers around it change with it."""
    stops = list(route.stops.select_related("place", "task").order_by("sequence"))
    pv = route.plan_vehicle
    position = (pv.start_latitude, pv.start_longitude)
    speed = pv.vehicle.average_speed_kph if pv.vehicle_id else None
    clock = pv.available_from or timezone.now()

    onboard_kg = onboard_cbm = Decimal("0")
    total_km = Decimal("0")
    total_drive_minutes = Decimal("0")
    peak_kg = peak_cbm = Decimal("0")
    revenue = Decimal("0")
    dead_km = Decimal("0")

    for index, stop in enumerate(stops):
        leg_km, leg_min = matrix.distance_and_duration(position, (stop.place.latitude, stop.place.longitude), average_speed_kph=speed)
        leg_km = leg_km or Decimal("0")
        leg_min = leg_min or Decimal("0")
        if index == 0:
            dead_km = leg_km   # the leg to the first stop is by definition empty running

        if stop.task_id:
            if stop.stop_type == "pickup":
                onboard_kg += stop.task.weight_kg
                onboard_cbm += stop.task.volume_cbm or 0
                revenue += stop.task.revenue_estimate or 0
            elif stop.stop_type == "drop":
                onboard_kg -= stop.task.weight_kg
                onboard_cbm -= stop.task.volume_cbm or 0

        arrival = clock + timedelta(minutes=float(leg_min))
        departure = arrival + timedelta(minutes=stop.service_minutes or 0)
        stop.distance_from_previous_km = leg_km
        stop.load_after_kg = onboard_kg
        stop.load_after_cbm = onboard_cbm
        stop.planned_arrival = arrival
        stop.planned_departure = departure
        stop.save(update_fields=["distance_from_previous_km", "load_after_kg", "load_after_cbm",
                                 "planned_arrival", "planned_departure", "updated_at"])

        total_km += leg_km
        total_drive_minutes += leg_min
        peak_kg = max(peak_kg, onboard_kg)
        peak_cbm = max(peak_cbm, onboard_cbm)
        position = (stop.place.latitude, stop.place.longitude)
        clock = departure

    cost = total_km * (pv.cost_per_km or 0) + (total_drive_minutes / Decimal("60")) * (pv.cost_per_hour or 0)
    if stops:
        cost += pv.fixed_cost or Decimal("0")
    capacity = pv.capacity_kg or Decimal("1")

    route.total_distance_km = total_km
    route.dead_km = dead_km
    route.drive_minutes = int(total_drive_minutes)
    route.total_duration_minutes = int(total_drive_minutes) + route.wait_minutes
    route.max_load_kg = peak_kg
    route.estimated_cost = money(cost)
    route.estimated_revenue = money(revenue)
    route.estimated_margin = money(revenue - cost)
    route.utilisation_weight_percent = money(min(Decimal("100"), peak_kg / capacity * 100)) if capacity else Decimal("0")
    route.utilisation_volume_percent = money(min(Decimal("100"), peak_cbm / pv.capacity_cbm * 100)) if pv.capacity_cbm else None
    route.save()
    return route


def _detach_task_stops(task, plan):
    """Remove `task`'s drop stop from wherever it sits in `plan`, and the
    pickup stop that fed it - but only when no other task's drop still falls
    between that pickup and the next one, since a cluster's pickup is shared.
    Returns the route the task was removed from, or None if it was not routed
    at all. Closes the sequence gap left behind so a later reorder still sees
    contiguous positions."""
    from ..models import PlannedStop
    drop_stop = PlannedStop.objects.filter(route__plan=plan, task=task, stop_type="drop").select_related("route").first()
    if drop_stop is None:
        return None
    route = drop_stop.route

    pickup_stop = PlannedStop.objects.filter(route=route, stop_type="pickup", sequence__lt=drop_stop.sequence) \
                                     .order_by("-sequence").first()
    pickup_still_needed = False
    if pickup_stop:
        next_pickup = PlannedStop.objects.filter(route=route, stop_type="pickup", sequence__gt=pickup_stop.sequence) \
                                         .order_by("sequence").first()
        other_drops = PlannedStop.objects.filter(route=route, stop_type="drop", sequence__gt=pickup_stop.sequence) \
                                         .exclude(pk=drop_stop.pk)
        if next_pickup:
            other_drops = other_drops.filter(sequence__lt=next_pickup.sequence)
        pickup_still_needed = other_drops.exists()

    drop_stop.delete()
    if pickup_stop and not pickup_still_needed:
        pickup_stop.delete()

    for index, stop in enumerate(route.stops.order_by("sequence"), start=1):
        if stop.sequence != index:
            stop.sequence = index
            stop.save(update_fields=["sequence", "updated_at"])
    return route


def move_task(plan, task, to_route):
    """Move `task` onto `to_route`, appended at the end as its own pickup and
    drop pair - it does not try to merge into an existing cluster's shared
    pickup on the destination route, so the move is always unambiguous."""
    from ..models import PlannedStop
    from django.db.models import Max

    from_route = _detach_task_stops(task, plan)
    next_sequence = (to_route.stops.aggregate(Max("sequence"))["sequence__max"] or 0) + 1
    PlannedStop.objects.create(route=to_route, sequence=next_sequence, task=None, place=task.pickup, stop_type="pickup",
                               service_minutes=task.pickup_service_minutes)
    PlannedStop.objects.create(route=to_route, sequence=next_sequence + 1, task=task, place=task.dropoff, stop_type="drop",
                               service_minutes=task.drop_service_minutes)
    task.status = "planned"
    task.drop_reason = ""
    task.save(update_fields=["status", "drop_reason", "updated_at"])

    if from_route and from_route.pk != to_route.pk:
        recompute_route(from_route)
    return recompute_route(to_route)


def unroute_task(plan, task):
    """Take `task` off whatever route carries it, and free it up for the next
    solve (or another manual placement) by putting it back to pending."""
    route = _detach_task_stops(task, plan)
    task.status = "pending"
    task.drop_reason = ""
    task.save(update_fields=["status", "drop_reason", "updated_at"])
    if route:
        recompute_route(route)
    return route


def reorder_route(route, stop_ids):
    """Resequence `route`'s stops to match `stop_ids` exactly, then recost.
    Raises `ValueError` if `stop_ids` is not exactly this route's current set
    of stop ids - a partial or foreign list would silently orphan stops."""
    stops_by_id = {stop.id: stop for stop in route.stops.all()}
    if set(stop_ids) != set(stops_by_id):
        raise ValueError("stop_ids must be exactly this route's current stop ids, once each.")
    # Two passes through placeholders far outside any real route's stop count:
    # an arbitrary permutation can ask for a sequence another stop still
    # holds, which unique_together("route", "sequence") would reject
    # mid-loop otherwise. `sequence` is a PositiveIntegerField, so the
    # placeholders have to stay non-negative too.
    for offset, stop_id in enumerate(stop_ids, start=1):
        stops_by_id[stop_id].sequence = 100000 + offset
        stops_by_id[stop_id].save(update_fields=["sequence", "updated_at"])
    for index, stop_id in enumerate(stop_ids, start=1):
        stops_by_id[stop_id].sequence = index
        stops_by_id[stop_id].save(update_fields=["sequence", "updated_at"])
    return recompute_route(route)
