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

**Strategies.** The cost function and the own-vs-outsource threshold are no
longer hardcoded - both read from a `strategies.Strategy` passed into `solve`,
so "cheapest plan" and "keep my own trucks full" are different runs of the
same code rather than two different solvers. See docs/DISPATCH-PLANNER-V2.md §3.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from . import matrix

PRECOOL_MINUTES = 45
WINDOW_MISS_PENALTY = Decimal("500")  # historical default; strategies.DEFAULT_WEIGHTS carries the real one


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
        # A dispatcher's manual pin (docs/DISPATCH-PLANNER-V2.md §8.1) - every
        # pinned task in the cluster has to agree on the vehicle, or the pin
        # cannot be honoured at all.
        self.pinned_vehicle_ids = {t.pinned_vehicle_id for t in tasks if t.pinned_vehicle_id}


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
        self.dead_km = Decimal("0")
        self.cost = Decimal("0")
        self.used = False


def _evaluate_cluster(route, cluster, strategy):
    """Feasibility and marginal cost of appending `cluster` to the end of
    `route`, or None with a reason when it cannot fit. `strategy` supplies the
    cost weights (see strategies.py) and any constraint overrides."""
    pv = route.plan_vehicle
    w = strategy.weights
    hard_windows = strategy.constraints.get("time_windows") == "hard"
    max_stops = strategy.constraints.get("max_stops_per_route") or pv.max_stops
    max_route_km = strategy.constraints.get("max_route_km") or pv.max_route_km
    max_duty_minutes = strategy.constraints.get("max_duty_minutes") or pv.max_duty_minutes

    if not temperature_compatible(pv.temperature_class, "chiller" if cluster.needs_cooling else "dry"):
        return None, "temperature class mismatch"
    if cluster.weight_kg > pv.capacity_kg:
        return None, "exceeds weight capacity"
    if pv.capacity_cbm and cluster.volume_cbm > pv.capacity_cbm:
        return None, "exceeds volume capacity"
    if len(route.stops) + 1 + len(cluster.tasks) > max_stops:
        return None, "exceeds max stops"

    speed = pv.vehicle.average_speed_kph if pv.vehicle_id else None
    to_pickup_km, to_pickup_min = matrix.distance_and_duration(
        route.position, (cluster.pickup.latitude, cluster.pickup.longitude), average_speed_kph=speed)
    if to_pickup_km is None:
        return None, "missing coordinates"

    # The leg from wherever the vehicle currently is to this cluster's pickup
    # carries no load - it is dead running by definition (docs/DISPATCH-PLANNER-V2.md §5.1).
    dead_km = to_pickup_km
    added_km = to_pickup_km
    added_drive_minutes = to_pickup_min
    arrival = route.time + timedelta(minutes=float(to_pickup_min))
    violations = []

    first_task = cluster.tasks[0]
    added_wait_minutes = Decimal("0")
    if first_task.pickup_window_start and arrival < first_task.pickup_window_start:
        # The gate is not open yet - the vehicle waits, it does not load early.
        added_wait_minutes = Decimal(str((first_task.pickup_window_start - arrival).total_seconds() / 60))
        arrival = first_task.pickup_window_start
    if first_task.pickup_window_end and arrival > first_task.pickup_window_end:
        if hard_windows:
            return None, f"{cluster.pickup.name}: arrival misses the loading window"
        violations.append(f"{cluster.pickup.name}: arrival misses the loading window")

    departure = arrival + timedelta(minutes=first_task.pickup_service_minutes)
    if cluster.needs_cooling:
        departure += timedelta(minutes=PRECOOL_MINUTES)
    onboard_kg = cluster.weight_kg
    onboard_cbm = cluster.volume_cbm
    stop_plan = [{"place": cluster.pickup, "stop_type": "pickup", "task": None,
                 "arrival": arrival, "departure": departure, "distance_km": to_pickup_km,
                 "load_kg": onboard_kg, "load_cbm": onboard_cbm, "wait_minutes": int(added_wait_minutes)}]

    position = (cluster.pickup.latitude, cluster.pickup.longitude)
    for task in cluster.tasks:
        leg_km, leg_min = matrix.distance_and_duration(
            position, (task.dropoff.latitude, task.dropoff.longitude), average_speed_kph=speed)
        if leg_km is None:
            return None, "missing coordinates"
        added_km += leg_km
        added_drive_minutes += leg_min
        arrival = departure + timedelta(minutes=float(leg_min))
        if task.drop_window_end and arrival > task.drop_window_end:
            if hard_windows:
                return None, f"{task.dropoff.name}: arrival misses the delivery window"
            violations.append(f"{task.dropoff.name}: arrival misses the delivery window")
        departure = arrival + timedelta(minutes=task.drop_service_minutes)
        onboard_kg -= task.weight_kg
        onboard_cbm -= (task.volume_cbm or 0)
        stop_plan.append({"place": task.dropoff, "stop_type": "drop", "task": task,
                          "arrival": arrival, "departure": departure, "distance_km": leg_km,
                          "load_kg": onboard_kg, "load_cbm": onboard_cbm, "wait_minutes": 0})
        position = (task.dropoff.latitude, task.dropoff.longitude)

    if route.distance_km + added_km > max_route_km:
        return None, "exceeds max route distance"
    if route.drive_minutes + Decimal(str(added_drive_minutes)) > max_duty_minutes:
        return None, "exceeds driver duty hours"

    hours = Decimal(str(added_drive_minutes)) / Decimal("60")
    cost = (added_km * pv.cost_per_km * Decimal(str(w["distance_cost"]))
           + hours * pv.cost_per_hour * Decimal(str(w["time_cost"]))
           + len(violations) * Decimal(str(w["window_miss_penalty"]))
           + dead_km * Decimal(str(w["dead_km_penalty"])))
    if not route.used:
        cost += pv.fixed_cost * Decimal(str(w["fixed_cost"]))
    if pv.source == "own":
        cost -= Decimal(str(w["own_fleet_discount"]))
    if pv.capacity_kg:
        fill_percent = (cluster.weight_kg / pv.capacity_kg) * 100
        cost -= fill_percent * Decimal(str(w["utilisation_bonus"]))
    if w["margin_weight"]:
        cost -= cluster.revenue_estimate * Decimal(str(w["margin_weight"]))

    return {"stop_plan": stop_plan, "added_km": added_km, "added_drive_minutes": Decimal(str(added_drive_minutes)),
           "added_wait_minutes": added_wait_minutes, "dead_km": dead_km, "cost": cost, "violations": violations,
           "final_position": position, "final_time": departure}, None


def _apply(route, cluster, evaluation):
    route.stops.extend(evaluation["stop_plan"])
    route.distance_km += evaluation["added_km"]
    route.drive_minutes += evaluation["added_drive_minutes"]
    route.wait_minutes += evaluation["added_wait_minutes"]
    route.dead_km += evaluation["dead_km"]
    route.position = evaluation["final_position"]
    route.time = evaluation["final_time"]
    route.cost += evaluation["cost"]
    route.used = True


def solve(plan_vehicles, tasks, strategy=None):
    """Returns (routes, outsourced, skipped).

    `routes` is every `RouteState` touched (used or not - the caller decides
    whether an empty route is worth persisting). `outsourced` is
    `[(cluster, reason), ...]`. `skipped` is tasks with no usable coordinates,
    which never reach the clustering stage at all.

    `strategy` is a `strategies.Strategy` (see that module); omitting it runs
    the "balanced" preset, matching this function's behaviour before
    strategies existed.
    """
    if strategy is None:
        from ..strategies import Strategy
        strategy = Strategy()

    clusters, skipped = build_clusters(tasks)
    clusters.sort(key=lambda c: (not c.must_go, -float(c.revenue_estimate or 0)))

    candidate_vehicles = [pv for pv in plan_vehicles if not pv.excluded]
    routes = [RouteState(pv) for pv in candidate_vehicles]

    allow_partial = strategy.constraints.get("allow_partial_service", True)
    outsource_bias = Decimal(str(strategy.weights["outsource_bias"]))

    outsourced = []
    for cluster in clusters:
        if len(cluster.pinned_vehicle_ids) > 1:
            # Two tasks sharing this pickup are pinned to different vehicles -
            # no single route can honour both, so neither pin can be kept.
            if not allow_partial:
                raise RuntimeError(
                    f"The load from {cluster.pickup.name} has conflicting vehicle pins, "
                    f"and allow_partial_service is False.")
            outsourced.append((cluster, "conflicting vehicle pins"))
            continue

        eligible_routes = routes
        if cluster.pinned_vehicle_ids:
            pinned_id = next(iter(cluster.pinned_vehicle_ids))
            eligible_routes = [r for r in routes if r.plan_vehicle.vehicle_id == pinned_id]

        best_route, best_eval, best_reason = None, None, None
        for route in eligible_routes:
            evaluation, reason = _evaluate_cluster(route, cluster, strategy)
            if evaluation is None:
                best_reason = best_reason or reason
                continue
            if best_eval is None or evaluation["cost"] < best_eval["cost"]:
                best_route, best_eval = route, evaluation

        if best_route is None:
            if not allow_partial:
                raise RuntimeError(
                    f"No eligible vehicle for the load from {cluster.pickup.name} "
                    f"({best_reason or 'no eligible vehicle'}), and allow_partial_service is False.")
            outsourced.append((cluster, best_reason or (
                "pinned vehicle is not in this plan or cannot take the load" if cluster.pinned_vehicle_ids
                else "no eligible vehicle")))
            continue

        threshold = cluster.outsource_estimate * outsource_bias
        if cluster.must_go or cluster.pinned_vehicle_ids or not threshold or best_eval["cost"] <= threshold:
            _apply(best_route, cluster, best_eval)
        else:
            if not allow_partial:
                raise RuntimeError(
                    f"The load from {cluster.pickup.name} would be outsourced as cheaper, "
                    f"and allow_partial_service is False.")
            outsourced.append((cluster, "cheaper on the spot market"))

    return routes, outsourced, skipped
