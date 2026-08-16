"""Orchestrates one solve: build vehicles, run the greedy construction, persist
routes/stops, group outsourced clusters into hire requirements, and summarise
the plan. See docs/DISPATCH-PLANNING.md §5 and §6, and docs/DISPATCH-PLANNER-V2.md §3.
"""
import time as _time
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from fleet.models import money

from . import inputs
from .greedy import solve as greedy_solve
from ..models import DispatchTask, HireRequirement, PlanEvent, PlannedRoute, PlannedStop, PlanVehicle
from ..strategies import check_constraints, resolve_strategy


@transaction.atomic
def solve_plan(plan, strategy=None):
    """`strategy` is a `strategies.Strategy`; when omitted it is resolved from
    `plan.objective` (falling back to "balanced"), and the resolved vector is
    always written back to `plan.objective` so the plan stays reproducible -
    re-opening it later shows exactly what it was solved with."""
    started = _time.monotonic()
    plan.status = "solving"
    if strategy is None:
        strategy = resolve_strategy(plan.objective)
    plan.objective = strategy.as_dict()
    plan.save(update_fields=["status", "objective", "updated_at"])

    plan_vehicles = list(PlanVehicle.objects.filter(plan=plan))
    tasks = list(DispatchTask.objects.filter(plan=plan, status="pending").select_related("pickup", "dropoff"))

    PlannedRoute.objects.filter(plan=plan).delete()
    HireRequirement.objects.filter(plan=plan, status="open").delete()

    if plan.solver == "ortools":
        try:
            from .ortools_solver import solve as ortools_solve
            routes, outsourced, skipped = ortools_solve(plan_vehicles, tasks, strategy=strategy, horizon_hours=plan.horizon_hours)
        except ImportError:
            routes, outsourced, skipped = greedy_solve(plan_vehicles, tasks, strategy)
            plan.solver = "greedy"
    else:
        routes, outsourced, skipped = greedy_solve(plan_vehicles, tasks, strategy)

    committed_tasks, dropped_tasks = set(), set()
    total_revenue = total_cost = Decimal("0")
    total_tonne_km = Decimal("0")
    own_fleet_value = Decimal("0")
    route_count = 0
    weight_utils, volume_utils = [], []
    OWN_SOURCES = ("own", "attached", "leased")

    for sequence, route in enumerate(routes, start=1):
        if not route.used:
            continue
        route_count += 1
        planned_route = PlannedRoute.objects.create(
            plan=plan, plan_vehicle=route.plan_vehicle, sequence=sequence,
            total_distance_km=route.distance_km, total_duration_minutes=int(route.drive_minutes + route.wait_minutes),
            drive_minutes=int(route.drive_minutes), wait_minutes=int(route.wait_minutes), dead_km=route.dead_km,
            temperature_class=route.plan_vehicle.temperature_class, estimated_cost=money(route.cost))

        capacity = route.plan_vehicle.capacity_kg or Decimal("1")
        vol_capacity = route.plan_vehicle.capacity_cbm or None
        revenue = Decimal("0")
        peak_kg = Decimal("0")
        peak_cbm = Decimal("0")
        for stop_no, stop in enumerate(route.stops, start=1):
            task = stop["task"]
            if task is not None:
                revenue += task.revenue_estimate or 0
                task.status = "planned"
                task.save(update_fields=["status", "updated_at"])
                committed_tasks.add(task.pk)
            peak_kg = max(peak_kg, stop["load_kg"])
            peak_cbm = max(peak_cbm, stop["load_cbm"])
            PlannedStop.objects.create(
                route=planned_route, sequence=stop_no, task=task, place=stop["place"], stop_type=stop["stop_type"],
                planned_arrival=stop["arrival"], planned_departure=stop["departure"],
                service_minutes=int((stop["departure"] - stop["arrival"]).total_seconds() // 60),
                wait_minutes=int(stop.get("wait_minutes", 0)),
                load_after_kg=stop["load_kg"], load_after_cbm=stop["load_cbm"], distance_from_previous_km=stop["distance_km"])

        planned_route.max_load_kg = peak_kg
        planned_route.estimated_revenue = money(revenue)
        planned_route.estimated_margin = money(Decimal(str(revenue)) - route.cost)
        planned_route.utilisation_weight_percent = money(min(Decimal("100"), peak_kg / capacity * 100)) if capacity else Decimal("0")
        # None (not 0) when the vehicle's volume capacity was never recorded -
        # a genuinely empty reefer and an un-measured dry truck must not look
        # the same on a utilisation chart (docs/DISPATCH-PLANNER-V2.md §5.1).
        planned_route.utilisation_volume_percent = (
            money(min(Decimal("100"), peak_cbm / vol_capacity * 100)) if vol_capacity else None)
        planned_route.save()
        total_revenue += revenue
        total_cost += route.cost
        total_tonne_km += (peak_kg / Decimal("1000")) * route.distance_km
        weight_utils.append(planned_route.utilisation_weight_percent)
        if planned_route.utilisation_volume_percent is not None:
            volume_utils.append(planned_route.utilisation_volume_percent)
        if route.plan_vehicle.source in OWN_SOURCES:
            own_fleet_value += revenue

    # Group outsourced clusters into hire requirements by pickup, so an RFQ goes
    # out once per lane rather than once per dropped load.
    by_pickup = {}
    for cluster, reason in outsourced:
        for task in cluster.tasks:
            task.status = "outsourced"
            task.drop_reason = reason
            task.save(update_fields=["status", "drop_reason", "updated_at"])
            dropped_tasks.add(task.pk)
        key = cluster.pickup.pk
        by_pickup.setdefault(key, {"pickup": cluster.pickup, "tasks": [], "cost": Decimal("0")})
        by_pickup[key]["tasks"].extend(cluster.tasks)
        by_pickup[key]["cost"] += cluster.outsource_estimate

    outsourced_value = Decimal("0")
    for group in by_pickup.values():
        requirement = HireRequirement.objects.create(
            plan=plan, vehicle_type=group["tasks"][0].allowed_vehicle_types[0] if group["tasks"][0].allowed_vehicle_types else "",
            temperature_class=group["tasks"][0].temperature_class, capacity_kg=sum((t.weight_kg for t in group["tasks"]), Decimal("0")),
            pickup=group["pickup"], dropoff=group["tasks"][0].dropoff, estimated_cost=money(group["cost"]))
        requirement.tasks.set(group["tasks"])
        total_cost += group["cost"]
        outsourced_value += group["cost"]

    for task in skipped:
        task.status = "dropped"
        task.drop_reason = "Pickup or dropoff has no coordinates - cannot be routed"
        task.save(update_fields=["status", "drop_reason", "updated_at"])

    elapsed = Decimal(str(round(_time.monotonic() - started, 2)))
    total_tasks = len(tasks) + len(skipped)
    served = len(committed_tasks)
    used_routes = [r for r in routes if r.used]
    total_distance = sum((r.distance_km for r in used_routes), Decimal("0"))
    total_dead_km = sum((r.dead_km for r in used_routes), Decimal("0"))
    total_stops = sum((len(r.stops) for r in used_routes), 0)
    total_duration_hours = sum(((r.drive_minutes + r.wait_minutes) for r in used_routes), Decimal("0")) / Decimal("60")

    windowed_stops = list(PlannedStop.objects.filter(route__plan=plan, task__isnull=False, task__drop_window_end__isnull=False)
                                             .select_related("task"))
    on_time_count = sum(1 for stop in windowed_stops if stop.planned_arrival and stop.planned_arrival <= stop.task.drop_window_end)

    own_hire_total = own_fleet_value + outsourced_value
    tasks_by_temperature = {}
    for task in list(tasks) + list(skipped):
        tasks_by_temperature[task.temperature_class] = tasks_by_temperature.get(task.temperature_class, 0) + 1

    plan.summary = {
        "total_tasks": total_tasks, "served_own_fleet": served, "outsourced": len(dropped_tasks),
        "dropped_unroutable": len(skipped), "fill_rate_percent": float(round(served / total_tasks * 100, 1)) if total_tasks else 0.0,
        "routes_used": route_count, "total_distance_km": float(total_distance),
        "total_revenue": float(money(total_revenue)), "total_cost": float(money(total_cost)),
        "total_margin": float(money(total_revenue - total_cost)),
        "strategy": strategy.preset,
        "total_dead_km": float(total_dead_km),
        "dead_km_percent": float(round(total_dead_km / total_distance * 100, 1)) if total_distance else 0.0,
        "avg_weight_utilisation": float(round(sum(weight_utils, Decimal("0")) / len(weight_utils), 1)) if weight_utils else 0.0,
        "avg_volume_utilisation": float(round(sum(volume_utils, Decimal("0")) / len(volume_utils), 1)) if volume_utils else None,
        "cost_per_tonne_km": float(round(total_cost / total_tonne_km, 2)) if total_tonne_km else None,
        "own_fleet_value": float(money(own_fleet_value)), "outsourced_value": float(money(outsourced_value)),
        "own_vs_hire_percent": float(round(own_fleet_value / own_hire_total * 100, 1)) if own_hire_total else None,
        "projected_on_time_percent": float(round(on_time_count / len(windowed_stops) * 100, 1)) if windowed_stops else None,
        "stops_per_route": float(round(total_stops / route_count, 1)) if route_count else 0.0,
        "avg_route_duration_hours": float(round(total_duration_hours / route_count, 1)) if route_count else 0.0,
        "tasks_by_temperature": tasks_by_temperature,
    }
    plan.summary["constraint_breaches"] = check_constraints(strategy, plan.summary)
    plan.status = "solved"
    plan.solver_seconds = elapsed
    plan.solver_status = f"{route_count} route(s), {len(dropped_tasks)} outsourced, {len(skipped)} unroutable"
    plan.save(update_fields=["status", "solver_seconds", "solver_status", "summary", "solver", "updated_at"])
    plan.log("solved", plan.solver_status, {"summary": plan.summary})
    return plan
