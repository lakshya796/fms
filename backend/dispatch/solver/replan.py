"""Re-planning: when a committed plan's execution drifts, build a fresh child
plan for whatever has not yet been picked up, leaving anything already in
motion untouched. See docs/DISPATCH-PLANNING.md §7.3.

Scope, stated plainly: a route is only pulled into the child plan when its own
pickup stop has not yet been arrived - the vehicle has not collected anything
yet, so its tasks can be freely reassigned to any vehicle, including a
different one. A route already mid-delivery (some drops made, cargo still
aboard for the rest) is left running exactly as committed; re-flowing an
already-loaded vehicle's remaining drops needs the vehicle-state tracking a
full PDPTW solver carries (the OR-Tools phase), not this construction - track
it with `PlannedStopViewSet.arrive`/`depart` instead.
"""
from django.db import transaction
from django.utils import timezone

from . import inputs
from .engine import solve_plan
from ..strategies import resolve_strategy


@transaction.atomic
def replan(parent, *, created_by=""):
    if parent.status != "committed":
        raise ValueError("Only a committed plan can be re-planned.")

    child = parent.__class__.objects.create(
        branch=parent.branch, plan_date=parent.plan_date, horizon_hours=parent.horizon_hours,
        parent_plan=parent, created_by=created_by)

    fresh_tasks = inputs.collect_tasks(child)
    fresh_vehicles = {pv.vehicle_id: pv for pv in inputs.build_plan_vehicles(child) if pv.vehicle_id}
    inputs.build_spot_slot_vehicles(child)

    from ..models import DispatchTask
    carried = []
    routes = parent.routes.filter(committed_trip__isnull=False).select_related("plan_vehicle__vehicle") \
                          .prefetch_related("stops__task__order")
    for route in routes:
        pickup_stop = route.stops.filter(stop_type="pickup").order_by("sequence").first()
        if pickup_stop is None or pickup_stop.actual_arrival is not None:
            continue  # already collected - stays on the committed route, untouched

        vehicle = route.plan_vehicle.vehicle
        plan_vehicle = fresh_vehicles.get(vehicle.pk) if vehicle else None
        if plan_vehicle is not None and vehicle.current_latitude is not None and vehicle.current_longitude is not None:
            plan_vehicle.start_latitude = vehicle.current_latitude
            plan_vehicle.start_longitude = vehicle.current_longitude
            plan_vehicle.available_from = timezone.now()
            plan_vehicle.position_stale = False
            plan_vehicle.save(update_fields=["start_latitude", "start_longitude", "available_from",
                                             "position_stale", "updated_at"])

        for stop in route.stops.filter(task__isnull=False).order_by("sequence"):
            task = stop.task
            carried.append(DispatchTask.objects.create(
                plan=child, order=task.order, indent=task.indent, task_type=task.task_type,
                pickup=task.pickup, dropoff=task.dropoff, weight_kg=task.weight_kg, volume_cbm=task.volume_cbm,
                packages=task.packages, temperature_class=task.temperature_class, temp_set_point_c=task.temp_set_point_c,
                pickup_window_start=task.pickup_window_start, pickup_window_end=task.pickup_window_end,
                drop_window_start=task.drop_window_start, drop_window_end=task.drop_window_end,
                pickup_service_minutes=task.pickup_service_minutes, drop_service_minutes=task.drop_service_minutes,
                priority=task.priority, allowed_vehicle_types=task.allowed_vehicle_types,
                banned_vehicles=task.banned_vehicles, revenue_estimate=task.revenue_estimate,
                outsource_estimate=task.outsource_estimate))
        # This vehicle's original route is superseded by the child's re-solve -
        # nothing was collected yet, so nothing real is disturbed by re-flowing it.
        route.locked = True
        route.save(update_fields=["locked", "updated_at"])

    parent.status = "superseded"
    parent.save(update_fields=["status", "updated_at"])
    parent.log("replanned", f"Re-planned into {child.code}: {len(carried)} carried task(s), {len(fresh_tasks)} new demand")

    child.status = "ready"
    child.save(update_fields=["status", "updated_at"])
    # The re-plan keeps the parent's strategy - a dispatcher who chose
    # own_fleet_first should not have that silently reset to balanced.
    solve_plan(child, resolve_strategy(parent.objective))
    child.refresh_from_db()
    return child
