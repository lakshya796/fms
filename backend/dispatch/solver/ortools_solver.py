"""OR-Tools encoding of the same CVRP+PD problem `greedy.py` solves
heuristically - full interleaving across different pickups on one route, which
is the Phase F upgrade path described in docs/DISPATCH-PLANNING.md §6.10.

Same call signature and the same stop-dict shape as `greedy.solve`, so
`engine.py` does not know or care which one ran. Only imported when
`DispatchPlan.solver == "ortools"`; `engine.solve_plan` falls back to
`greedy.solve` if the `ortools` package is not installed, so this module is a
true drop-in rather than a hard dependency of the app.

Money is scaled to paise and distance to metres before reaching OR-Tools,
which works in integers - the same convention docs/DISPATCH-PLANNING.md §6.1
specifies.

**Strategies.** The same weight vector `greedy.py` reads (see strategies.py)
maps onto this model's arc costs, fixed vehicle costs, disjunction penalty and
soft/hard time windows - `distance_cost`, `time_cost`, `fixed_cost`,
`window_miss_penalty`, `outsource_bias`, `own_fleet_discount` and
`dead_km_penalty` (applied to each vehicle's first, necessarily empty, arc).
`utilisation_bonus` and `margin_weight` are not linear arc costs and are not
encoded here - see docs/DISPATCH-PLANNER-V2.md §3.2.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from . import matrix
from .greedy import Cluster, RouteState, WINDOW_MISS_PENALTY, temperature_compatible

MONEY_SCALE = 100          # rupees -> paise
KM_SCALE = 1000            # km -> metres
DEFAULT_TIME_LIMIT_SECONDS = 10
PRECOOL_SECONDS = 45 * 60


def solve(plan_vehicles, tasks, *, strategy=None, time_limit_seconds=None, horizon_hours=48):
    """Returns (routes, outsourced, skipped) - identical shape to `greedy.solve`.

    `horizon_hours` bounds the Time dimension's domain. It has to stay tight to
    the plan's real timeframe: a multi-day domain does not make the model more
    correct, it just gives the search a far larger neighbourhood to explore per
    node and turns a sub-second solve into one that never converges within its
    own time limit.

    `strategy` is a `strategies.Strategy`; omitting it runs the "balanced" preset.
    """
    if strategy is None:
        from ..strategies import Strategy
        strategy = Strategy()
    w = strategy.weights
    hard_windows = strategy.constraints.get("time_windows") == "hard"

    candidate_vehicles = [pv for pv in plan_vehicles if not pv.excluded]
    routable = [t for t in tasks if t.pickup.latitude is not None and t.pickup.longitude is not None
               and t.dropoff.latitude is not None and t.dropoff.longitude is not None]
    skipped = [t for t in tasks if t not in routable]

    if not candidate_vehicles or not routable:
        return [RouteState(pv) for pv in candidate_vehicles], [], skipped

    solve_start = timezone.now()

    # Node layout: 2 nodes per task (pickup, delivery), then one dummy
    # start/end node per vehicle - an open route with no forced return, since
    # every arc INTO a vehicle's own dummy node costs whatever the model gives
    # it (nothing forces the vehicle back there).
    nodes = []
    for task in routable:
        nodes.append(("pickup", task))
        nodes.append(("delivery", task))
    num_task_nodes = len(nodes)
    num_vehicles = len(candidate_vehicles)
    starts = list(range(num_task_nodes, num_task_nodes + num_vehicles))
    total_nodes = num_task_nodes + num_vehicles

    coords = []
    for kind, task in nodes:
        place = task.pickup if kind == "pickup" else task.dropoff
        coords.append((place.latitude, place.longitude))
    for pv in candidate_vehicles:
        coords.append((pv.start_latitude, pv.start_longitude))

    distance_m = [[0] * total_nodes for _ in range(total_nodes)]
    duration_s = [[0] * total_nodes for _ in range(total_nodes)]
    for i in range(total_nodes):
        for j in range(total_nodes):
            if i == j:
                continue
            km, minutes = matrix.distance_and_duration(coords[i], coords[j])
            distance_m[i][j] = int(float(km or 0) * KM_SCALE)
            duration_s[i][j] = int(float(minutes or 0) * 60)

    manager = pywrapcp.RoutingIndexManager(total_nodes, num_vehicles, starts, starts)
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index, to_index):
        return distance_m[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
    distance_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.AddDimension(distance_callback_index, 0, 10**9, True, "Distance")
    distance_dimension = routing.GetDimensionOrDie("Distance")

    def service_seconds(node):
        if node >= num_task_nodes:
            return 0
        kind, task = nodes[node]
        return (task.pickup_service_minutes if kind == "pickup" else task.drop_service_minutes) * 60

    def time_callback(from_index, to_index):
        i, j = manager.IndexToNode(from_index), manager.IndexToNode(to_index)
        return duration_s[i][j] + service_seconds(i)
    time_callback_index = routing.RegisterTransitCallback(time_callback)
    horizon_seconds = int(max(horizon_hours, 6) * 3600)
    routing.AddDimension(time_callback_index, horizon_seconds, horizon_seconds, False, "Time")
    time_dimension = routing.GetDimensionOrDie("Time")

    def weight_demand(index):
        node = manager.IndexToNode(index)
        if node >= num_task_nodes:
            return 0
        kind, task = nodes[node]
        value = int(float(task.weight_kg or 0) * 1000)  # grams, so a sub-1kg demand still registers
        return value if kind == "pickup" else -value
    weight_callback_index = routing.RegisterUnaryTransitCallback(weight_demand)
    routing.AddDimensionWithVehicleCapacity(
        weight_callback_index, 0, [int((pv.capacity_kg or 0) * 1000) for pv in candidate_vehicles], True, "Weight")

    def volume_demand(index):
        node = manager.IndexToNode(index)
        if node >= num_task_nodes:
            return 0
        kind, task = nodes[node]
        value = int(float(task.volume_cbm or 0) * 1000)  # litres
        return value if kind == "pickup" else -value
    volume_callback_index = routing.RegisterUnaryTransitCallback(volume_demand)
    volume_capacities = [int((pv.capacity_cbm or 0) * 1000) or 10**9 for pv in candidate_vehicles]
    routing.AddDimensionWithVehicleCapacity(volume_callback_index, 0, volume_capacities, True, "Volume")

    w_distance = Decimal(str(w["distance_cost"]))
    w_time = Decimal(str(w["time_cost"]))
    w_fixed = Decimal(str(w["fixed_cost"]))
    w_dead = Decimal(str(w["dead_km_penalty"]))
    w_own_discount = Decimal(str(w["own_fleet_discount"]))

    for v, pv in enumerate(candidate_vehicles):
        cost_per_km = pv.cost_per_km or Decimal("0")
        cost_per_second = (pv.cost_per_hour or Decimal("0")) / Decimal("3600")
        start_node = starts[v]
        is_own = pv.source == "own"

        def make_cost_callback(cpk=cost_per_km, cps=cost_per_second, start=start_node, own=is_own):
            def cb(from_index, to_index):
                i, j = manager.IndexToNode(from_index), manager.IndexToNode(to_index)
                km = Decimal(distance_m[i][j]) / KM_SCALE
                seconds = Decimal(duration_s[i][j])
                cost = km * cpk * w_distance + seconds * cps * w_time
                if i == start:
                    # The vehicle's first arc, wherever it lands, is by
                    # definition empty running - see greedy.py's `dead_km`.
                    cost += km * w_dead
                return int(cost * MONEY_SCALE)
            return cb
        cost_index = routing.RegisterTransitCallback(make_cost_callback())
        routing.SetArcCostEvaluatorOfVehicle(cost_index, v)
        fixed_cost = (pv.fixed_cost or 0) * w_fixed
        if is_own:
            fixed_cost -= w_own_discount
        routing.SetFixedCostOfVehicle(max(0, int(fixed_cost * MONEY_SCALE)), v)
        distance_dimension.SetSpanUpperBoundForVehicle(int((pv.max_route_km or 800) * KM_SCALE), v)
        available_seconds = int((pv.available_from - solve_start).total_seconds()) if pv.available_from else 0
        time_dimension.CumulVar(routing.Start(v)).SetMin(max(0, available_seconds))

    for t in range(0, num_task_nodes, 2):
        kind, task = nodes[t]
        pickup_index = manager.NodeToIndex(t)
        delivery_index = manager.NodeToIndex(t + 1)
        routing.AddPickupAndDelivery(pickup_index, delivery_index)
        routing.solver().Add(routing.VehicleVar(pickup_index) == routing.VehicleVar(delivery_index))
        routing.solver().Add(time_dimension.CumulVar(pickup_index) <= time_dimension.CumulVar(delivery_index))
        if task.temperature_class and task.temperature_class != "dry":
            routing.solver().Add(time_dimension.CumulVar(delivery_index)
                                 >= time_dimension.CumulVar(pickup_index) + PRECOOL_SECONDS)

        allowed = [v for v, pv in enumerate(candidate_vehicles) if temperature_compatible(pv.temperature_class, task.temperature_class)]
        if not allowed:
            # No vehicle in this plan can ever carry it - force the disjunction
            # so the model stays feasible instead of failing to solve at all.
            routing.AddDisjunction([pickup_index, delivery_index], 0)
            continue
        if len(allowed) < num_vehicles:
            routing.VehicleVar(pickup_index).SetValues(allowed)
            routing.VehicleVar(delivery_index).SetValues(allowed)

        if task.drop_window_end:
            deadline = int((task.drop_window_end - solve_start).total_seconds())
            if deadline > 0:
                if hard_windows:
                    time_dimension.CumulVar(delivery_index).SetMax(deadline)
                else:
                    window_penalty = Decimal(str(w["window_miss_penalty"]))
                    time_dimension.SetCumulVarSoftUpperBound(delivery_index, deadline, int(window_penalty * MONEY_SCALE))

        if task.priority != "must_go":
            outsource_bias = Decimal(str(w["outsource_bias"]))
            penalty = int((task.outsource_estimate or 0) * outsource_bias * MONEY_SCALE)
            routing.AddDisjunction([pickup_index, delivery_index], penalty)

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    search_parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.TABU_SEARCH
    search_parameters.time_limit.FromSeconds(time_limit_seconds or DEFAULT_TIME_LIMIT_SECONDS)

    solution = routing.SolveWithParameters(search_parameters)
    if solution is None:
        raise RuntimeError("OR-Tools found no feasible solution for this plan.")

    weight_dimension = routing.GetDimensionOrDie("Weight")
    volume_dimension = routing.GetDimensionOrDie("Volume")
    routes = [RouteState(pv) for pv in candidate_vehicles]
    visited_task_ids = set()

    for v, pv in enumerate(candidate_vehicles):
        route = routes[v]
        index = routing.Start(v)
        position = (pv.start_latitude, pv.start_longitude)
        first_leg = True
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            next_index = solution.Value(routing.NextVar(index))
            if node < num_task_nodes:
                kind, task = nodes[node]
                place = task.pickup if kind == "pickup" else task.dropoff
                arrival = solve_start + timedelta(seconds=solution.Min(time_dimension.CumulVar(index)))
                service_minutes = task.pickup_service_minutes if kind == "pickup" else task.drop_service_minutes
                departure = arrival + timedelta(minutes=service_minutes)
                leg_km, leg_minutes = matrix.distance_and_duration(position, (place.latitude, place.longitude))
                leg_km = leg_km or Decimal("0")
                route.stops.append({
                    "place": place, "stop_type": "pickup" if kind == "pickup" else "drop",
                    "task": task if kind == "delivery" else None, "arrival": arrival, "departure": departure,
                    "distance_km": leg_km,
                    "load_kg": Decimal(solution.Value(weight_dimension.CumulVar(index))) / 1000,
                    "load_cbm": Decimal(solution.Value(volume_dimension.CumulVar(index))) / 1000,
                })
                route.distance_km += leg_km
                route.drive_minutes += Decimal(str(leg_minutes or 0))
                route.cost += leg_km * (pv.cost_per_km or 0) + (Decimal(str(leg_minutes or 0)) / 60) * (pv.cost_per_hour or 0)
                route.used = True
                if first_leg:
                    # A vehicle's very first leg, to whatever it visits first,
                    # is by definition empty running (greedy.py's `dead_km`).
                    route.dead_km += leg_km
                    first_leg = False
                position = (place.latitude, place.longitude)
                if kind == "delivery":
                    visited_task_ids.add(task.pk)
            index = next_index
        if route.used:
            route.cost += pv.fixed_cost or Decimal("0")

    outsourced = [(Cluster(task.pickup, [task]), "not selected by the OR-Tools solve")
                 for task in routable if task.pk not in visited_task_ids]

    return routes, outsourced, skipped
