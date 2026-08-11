"""Greedy CVRP+PD construction: dependency-free, deterministic, and the CI
default and production fallback described in docs/DISPATCH-PLANNING.md §6.10.

**Scope, stated plainly.** Tasks that share one pickup place are grouped into a
`Cluster` and sequenced outward from that pickup by nearest neighbour; a route
serves one cluster's stops in full before starting the next. That covers
point-to-point FTL and the common "one plant, many drops" distribution run
exactly - a cluster of many deliveries loads once and unloads along an
efficient path - but it does not interleave pickups from two different origins
mid-route the way a full PDPTW encoding (OR-Tools, §6.3) would. That
interleaving is the Phase F upgrade; this module is deliberately the simpler,
auditable version that ships first.

Every cluster is either assigned to the cheapest feasible route or outsourced,
per §6.7: outsourcing is not a failure, it is buying the load on the market
when that costs less than running it - unless the cluster is `must_go`, which
cannot be dropped even at a loss.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from . import matrix

PRECOOL_MINUTES = 45
WINDOW_MISS_PENALTY = Decimal("500")


def temperature_compatible(vehicle_class, required_class):
    if not required_class or required_class == "dry":
        return True
    if vehicle_class == "multi":
        return True
    if required_class == "chiller":
        return vehicle_class in ("chiller", "frozen")
    return vehicle_class == required_class


def _sequence_by_nearest_neighbour(pickup, tasks):
    remaining = list(tasks)
    ordered = []
    position = (pickup.latitude, pickup.longitude)
    while remaining:
        best, best_km = None, None
        for task in remaining:
            km, _ = matrix.distance_and_duration(position, (task.dropoff.latitude, task.dropoff.longitude))
            if km is not None and (best_km is None or km < best_km):
                best, best_km = task, km
        best = best or remaining[0]
        ordered.append(best)
        remaining.remove(best)
        position = (best.dropoff.latitude, best.dropoff.longitude)
    return ordered


class Cluster:
    def __init__(self, pickup, tasks):
        self.pickup = pickup
        self.tasks = _sequence_by_nearest_neighbour(pickup, tasks)
        self.weight_kg = sum((t.weight_kg for t in tasks), Decimal("0"))
        self.volume_cbm = sum((t.volume_cbm for t in tasks), Decimal("0"))
        self.outsource_estimate = sum((t.outsource_estimate for t in tasks), Decimal("0"))
        self.revenue_estimate = sum((t.revenue_estimate for t in tasks), Decimal("0"))
        self.must_go = any(t.priority == "must_go" for t in tasks)
        self.needs_cooling = any(t.temperature_class != "dry" for t in tasks)


def build_clusters(tasks):
    groups = defaultdict(list)
    skipped = []
    for task in tasks:
        if task.pickup.latitude is None or task.pickup.longitude is None \
           or task.dropoff.latitude is None or task.dropoff.longitude is None:
            skipped.append(task)
            continue
        groups[task.pickup_id].append(task)
    clusters = [Cluster(tasks[0].pickup, tasks) for tasks in groups.values()]
    return clusters, skipped


class RouteState:
    def __init__(self, plan_vehicle):
        self.plan_vehicle = plan_vehicle
        self.stops = []                # list of dicts, in visit order
        self.position = (plan_vehicle.start_latitude, plan_vehicle.start_longitude)
        self.time = plan_vehicle.available_from
        self.distance_km = Decimal("0")
        self.drive_minutes = Decimal("0")
        self.wait_minutes = Decimal("0")
        self.cost = Decimal("0")
        self.used = False


def _evaluate_cluster(route, cluster):
    """Feasibility and marginal cost of appending `cluster` to the end of
    `route`, or None with a reason when it cannot fit."""
    pv = route.plan_vehicle
    if not temperature_compatible(pv.temperature_class, "chiller" if cluster.needs_cooling else "dry"):
        return None, "temperature class mismatch"
    if cluster.weight_kg > pv.capacity_kg:
        return None, "exceeds weight capacity"
    if pv.capacity_cbm and cluster.volume_cbm > pv.capacity_cbm:
        return None, "exceeds volume capacity"
    if len(route.stops) + 1 + len(cluster.tasks) > pv.max_stops:
        return None, "exceeds max stops"

    speed = pv.vehicle.average_speed_kph if pv.vehicle_id else None
    to_pickup_km, to_pickup_min = matrix.distance_and_duration(
        route.position, (cluster.pickup.latitude, cluster.pickup.longitude), average_speed_kph=speed)
    if to_pickup_km is None:
        return None, "missing coordinates"

    added_km = to_pickup_km
    added_drive_minutes = to_pickup_min
    arrival = route.time + timedelta(minutes=float(to_pickup_min))
    departure = arrival + timedelta(minutes=cluster.tasks[0].pickup_service_minutes)
    if cluster.needs_cooling:
        departure += timedelta(minutes=PRECOOL_MINUTES)
    onboard_kg = cluster.weight_kg
    onboard_cbm = cluster.volume_cbm
    stop_plan = [{"place": cluster.pickup, "stop_type": "pickup", "task": None,
                 "arrival": arrival, "departure": departure, "distance_km": to_pickup_km,
                 "load_kg": onboard_kg, "load_cbm": onboard_cbm}]

    position = (cluster.pickup.latitude, cluster.pickup.longitude)
    violations = []
    for task in cluster.tasks:
        leg_km, leg_min = matrix.distance_and_duration(
            position, (task.dropoff.latitude, task.dropoff.longitude), average_speed_kph=speed)
        if leg_km is None:
            return None, "missing coordinates"
        added_km += leg_km
        added_drive_minutes += leg_min
        arrival = departure + timedelta(minutes=float(leg_min))
        if task.drop_window_end and arrival > task.drop_window_end:
            violations.append(f"{task.dropoff.name}: arrival misses the delivery window")
        departure = arrival + timedelta(minutes=task.drop_service_minutes)
        onboard_kg -= task.weight_kg
        onboard_cbm -= (task.volume_cbm or 0)
        stop_plan.append({"place": task.dropoff, "stop_type": "drop", "task": task,
                          "arrival": arrival, "departure": departure, "distance_km": leg_km,
                          "load_kg": onboard_kg, "load_cbm": onboard_cbm})
        position = (task.dropoff.latitude, task.dropoff.longitude)

    if route.distance_km + added_km > pv.max_route_km:
        return None, "exceeds max route distance"
    if route.drive_minutes + Decimal(str(added_drive_minutes)) > pv.max_duty_minutes:
        return None, "exceeds driver duty hours"

    hours = Decimal(str(added_drive_minutes)) / Decimal("60")
    cost = added_km * pv.cost_per_km + hours * pv.cost_per_hour + len(violations) * WINDOW_MISS_PENALTY
    if not route.used:
        cost += pv.fixed_cost
    return {"stop_plan": stop_plan, "added_km": added_km, "added_drive_minutes": Decimal(str(added_drive_minutes)),
           "cost": cost, "violations": violations, "final_position": position, "final_time": departure}, None


def _apply(route, cluster, evaluation):
    route.stops.extend(evaluation["stop_plan"])
    route.distance_km += evaluation["added_km"]
    route.drive_minutes += evaluation["added_drive_minutes"]
    route.position = evaluation["final_position"]
    route.time = evaluation["final_time"]
    route.cost += evaluation["cost"]
    route.used = True


def solve(plan_vehicles, tasks):
    """Returns (routes, outsourced, skipped).

    `routes` is every `RouteState` touched (used or not - the caller decides
    whether an empty route is worth persisting). `outsourced` is
    `[(cluster, reason), ...]`. `skipped` is tasks with no usable coordinates,
    which never reach the clustering stage at all.
    """
    clusters, skipped = build_clusters(tasks)
    clusters.sort(key=lambda c: (not c.must_go, -float(c.revenue_estimate or 0)))

    candidate_vehicles = [pv for pv in plan_vehicles if not pv.excluded]
    routes = [RouteState(pv) for pv in candidate_vehicles]

    outsourced = []
    for cluster in clusters:
        best_route, best_eval, best_reason = None, None, None
        for route in routes:
            evaluation, reason = _evaluate_cluster(route, cluster)
            if evaluation is None:
                best_reason = best_reason or reason
                continue
            if best_eval is None or evaluation["cost"] < best_eval["cost"]:
                best_route, best_eval = route, evaluation

        if best_route is None:
            outsourced.append((cluster, best_reason or "no eligible vehicle"))
            continue
        if cluster.must_go or not cluster.outsource_estimate or best_eval["cost"] <= cluster.outsource_estimate:
            _apply(best_route, cluster, best_eval)
        else:
            outsourced.append((cluster, "cheaper on the spot market"))

    return routes, outsourced, skipped
