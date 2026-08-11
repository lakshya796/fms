"""Orchestrates one solve: build vehicles, run the greedy construction, persist
routes/stops, group outsourced clusters into hire requirements, and summarise
the plan. See docs/DISPATCH-PLANNING.md §5 and §6.
"""
import time as _time
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from fleet.models import money

from . import inputs
from .greedy import solve as greedy_solve
from ..models import DispatchTask, HireRequirement, PlanEvent, PlannedRoute, PlannedStop, PlanVehicle


@transaction.atomic
def solve_plan(plan):
    started = _time.monotonic()
    plan.status = "solving"
    plan.save(update_fields=["status", "updated_at"])

    plan_vehicles = list(PlanVehicle.objects.filter(plan=plan))
    tasks = list(DispatchTask.objects.filter(plan=plan, status="pending").select_related("pickup", "dropoff"))

    PlannedRoute.objects.filter(plan=plan).delete()
    HireRequirement.objects.filter(plan=plan, status="open").delete()

    if plan.solver == "ortools":
        try:
            from .ortools_solver import solve as ortools_solve
            routes, outsourced, skipped = ortools_solve(plan_vehicles, tasks)
        except ImportError:
            routes, outsourced, skipped = greedy_solve(plan_vehicles, tasks)
            plan.solver = "greedy"
    else:
        routes, outsourced, skipped = greedy_solve(plan_vehicles, tasks)

    committed_tasks, dropped_tasks = set(), set()
    total_revenue = total_cost = Decimal("0")
    route_count = 0

    for sequence, route in enumerate(routes, start=1):
        if not route.used:
            continue
        route_count += 1
        planned_route = PlannedRoute.objects.create(
            plan=plan, plan_vehicle=route.plan_vehicle, sequence=sequence,
            total_distance_km=route.distance_km, total_duration_minutes=int(route.drive_minutes + route.wait_minutes),
            drive_minutes=int(route.drive_minutes), wait_minutes=int(route.wait_minutes),
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
                load_after_kg=stop["load_kg"], load_after_cbm=stop["load_cbm"], distance_from_previous_km=stop["distance_km"])

        planned_route.max_load_kg = peak_kg
        planned_route.estimated_revenue = money(revenue)
        planned_route.estimated_margin = money(Decimal(str(revenue)) - route.cost)
        planned_route.utilisation_weight_percent = money(min(Decimal("100"), peak_kg / capacity * 100)) if capacity else Decimal("0")
        if vol_capacity:
            planned_route.utilisation_volume_percent = money(min(Decimal("100"), peak_cbm / vol_capacity * 100))
        planned_route.save()
        total_revenue += revenue
        total_cost += route.cost

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

    for group in by_pickup.values():
        requirement = HireRequirement.objects.create(
            plan=plan, vehicle_type=group["tasks"][0].allowed_vehicle_types[0] if group["tasks"][0].allowed_vehicle_types else "",
            temperature_class=group["tasks"][0].temperature_class, capacity_kg=sum((t.weight_kg for t in group["tasks"]), Decimal("0")),
            pickup=group["pickup"], dropoff=group["tasks"][0].dropoff, estimated_cost=money(group["cost"]))
        requirement.tasks.set(group["tasks"])
        total_cost += group["cost"]

    for task in skipped:
        task.status = "dropped"
        task.drop_reason = "Pickup or dropoff has no coordinates - cannot be routed"
        task.save(update_fields=["status", "drop_reason", "updated_at"])

    elapsed = Decimal(str(round(_time.monotonic() - started, 2)))
    total_tasks = len(tasks) + len(skipped)
    served = len(committed_tasks)
    plan.summary = {
        "total_tasks": total_tasks, "served_own_fleet": served, "outsourced": len(dropped_tasks),
        "dropped_unroutable": len(skipped), "fill_rate_percent": float(round(served / total_tasks * 100, 1)) if total_tasks else 0.0,
        "routes_used": route_count, "total_distance_km": float(sum((r.distance_km for r in routes if r.used), Decimal("0"))),
        "total_revenue": float(money(total_revenue)), "total_cost": float(money(total_cost)),
        "total_margin": float(money(total_revenue - total_cost)),
    }
    plan.status = "solved"
    plan.solver_seconds = elapsed
    plan.solver_status = f"{route_count} route(s), {len(dropped_tasks)} outsourced, {len(skipped)} unroutable"
    plan.save(update_fields=["status", "solver_seconds", "solver_status", "summary", "solver", "updated_at"])
    plan.log("solved", plan.solver_status, {"summary": plan.summary})
    return plan
