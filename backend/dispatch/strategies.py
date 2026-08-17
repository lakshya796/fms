"""Named planning strategies: what "solve this plan" means, made explicit and
tunable instead of the single hardcoded objective the solver used to have.

`DispatchPlan.objective` (models.py) already exists as a JSONField documented
as "Cost/service weight overrides for the solver" - nothing read it. This
module is what reads it: a small preset catalogue, a weight vector every
preset expands to, and a resolver that turns a request body (or a stored
plan.objective) into the `Strategy` the solver actually runs with.

See docs/DISPATCH-PLANNER-V2.md §3.
"""
from dataclasses import dataclass, field, replace
from decimal import Decimal

DEFAULT_WEIGHTS = {
    "distance_cost": 1.0,        # multiplier on cost_per_km x km
    "time_cost": 1.0,            # multiplier on cost_per_hour x hours
    "fixed_cost": 1.0,           # multiplier on starting a vehicle at all
    "window_miss_penalty": 500.0,  # rupees per missed window (was greedy.WINDOW_MISS_PENALTY)
    "outsource_bias": 1.0,       # x outsource_estimate before comparing; >1 favours own fleet
    "utilisation_bonus": 0.0,    # rupees credited per % of weight capacity filled
    "dead_km_penalty": 0.0,      # rupees per empty km run to reach a pickup
    "own_fleet_discount": 0.0,   # flat rupees shaved off an own vehicle's route cost
    "margin_weight": 0.0,        # rupees of revenue counted against cost when choosing
}

DEFAULT_CONSTRAINTS = {
    "max_outsource_percent": None,     # flag (not fail) a plan buying out more than this
    "min_utilisation_percent": None,   # flag a plan that starts trucks below this fill
    "time_windows": "soft",            # "soft" = penalty | "hard" = infeasible
    "max_stops_per_route": None,       # overrides PlanVehicle.max_stops when set
    "max_route_km": None,              # overrides PlanVehicle.max_route_km when set
    "max_duty_minutes": None,          # overrides PlanVehicle.max_duty_minutes when set
    "allow_partial_service": True,     # False = fail the solve rather than drop a task
}

# preset name -> {description, weight deltas over DEFAULT_WEIGHTS, constraint deltas}
STRATEGY_PRESETS = {
    "balanced": {
        "label": "Balanced",
        "description": "Even weighting across cost, service and utilisation - the default.",
        "weights": {},
        "constraints": {},
    },
    "least_cost": {
        "label": "Least cost",
        "description": "Cheapest total plan - outsource freely when the market is cheaper.",
        "weights": {},
        "constraints": {},
    },
    "max_utilisation": {
        "label": "Maximum utilisation",
        "description": "Keep own trucks as full as possible - outsource only when infeasible.",
        "weights": {"outsource_bias": 3.0, "utilisation_bonus": 40.0, "fixed_cost": 2.0},
        "constraints": {},
    },
    "fastest_service": {
        "label": "Fastest service",
        "description": "Hit every delivery window - spend more to do it.",
        "weights": {"window_miss_penalty": 5000.0, "time_cost": 2.0},
        "constraints": {"time_windows": "hard"},
    },
    "max_margin": {
        "label": "Maximum margin",
        "description": "Maximise revenue minus cost - drop unprofitable deferrable loads.",
        "weights": {"margin_weight": 1.0},
        "constraints": {},
    },
    "own_fleet_first": {
        "label": "Own fleet first",
        "description": "Exhaust own capacity before buying any market vehicle.",
        "weights": {"outsource_bias": 10.0, "own_fleet_discount": 500.0},
        "constraints": {},
    },
}

VALID_TIME_WINDOWS = {"soft", "hard"}


@dataclass(frozen=True)
class Strategy:
    preset: str = "balanced"
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    constraints: dict = field(default_factory=lambda: dict(DEFAULT_CONSTRAINTS))

    def as_dict(self):
        return {"preset": self.preset, "weights": dict(self.weights), "constraints": dict(self.constraints)}


class StrategyError(ValueError):
    pass


def _decimal_weights(weights):
    return {key: Decimal(str(value)) for key, value in weights.items()}


def resolve_strategy(payload=None):
    """Build a `Strategy` from a request body (or a stored `plan.objective` dict).

    `payload` may be:
      - None / {} -> the "balanced" preset, untouched
      - {"strategy": "<preset>", "weights": {...}, "constraints": {...}} -> preset
        expanded, then overridden by any keys given explicitly
      - {"preset": ..., "weights": ..., "constraints": ...} -> the shape
        `Strategy.as_dict()` produces, for round-tripping a stored objective

    Raises `StrategyError` for an unknown preset name or an invalid constraint.
    """
    payload = payload or {}
    preset_name = payload.get("strategy") or payload.get("preset") or "balanced"
    if preset_name not in STRATEGY_PRESETS:
        raise StrategyError(
            f"Unknown strategy '{preset_name}'. Choose one of: {', '.join(sorted(STRATEGY_PRESETS))}.")

    preset = STRATEGY_PRESETS[preset_name]
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(preset["weights"])
    weights.update(payload.get("weights") or {})

    unknown_weight_keys = set(weights) - set(DEFAULT_WEIGHTS)
    if unknown_weight_keys:
        raise StrategyError(f"Unknown weight(s): {', '.join(sorted(unknown_weight_keys))}.")
    for key, value in weights.items():
        try:
            weights[key] = float(value)
        except (TypeError, ValueError):
            raise StrategyError(f"Weight '{key}' must be a number.")

    constraints = dict(DEFAULT_CONSTRAINTS)
    constraints.update(preset["constraints"])
    constraints.update(payload.get("constraints") or {})

    unknown_constraint_keys = set(constraints) - set(DEFAULT_CONSTRAINTS)
    if unknown_constraint_keys:
        raise StrategyError(f"Unknown constraint(s): {', '.join(sorted(unknown_constraint_keys))}.")
    if constraints["time_windows"] not in VALID_TIME_WINDOWS:
        raise StrategyError('constraints.time_windows must be "soft" or "hard".')
    for key in ("max_outsource_percent", "min_utilisation_percent"):
        value = constraints[key]
        if value is not None:
            try:
                constraints[key] = float(value)
            except (TypeError, ValueError):
                raise StrategyError(f"constraints.{key} must be a number or null.")

    return Strategy(preset=preset_name, weights=weights, constraints=constraints)


def strategy_catalogue():
    """The preset list with each one's fully expanded vector, for the UI to
    build its picker from the server rather than hardcoding a copy."""
    out = []
    for name in STRATEGY_PRESETS:
        strategy = resolve_strategy({"strategy": name})
        out.append({"name": name, "label": STRATEGY_PRESETS[name]["label"],
                   "description": STRATEGY_PRESETS[name]["description"],
                   "weights": strategy.weights, "constraints": strategy.constraints})
    return out


def check_constraints(strategy, summary):
    """Post-solve check of `summary` against `strategy.constraints`. The plan
    is never failed by this - see docs/DISPATCH-PLANNER-V2.md §3.3 - only
    reported, so a dispatcher sees the breach rather than a plan that silently
    ignored what they asked for."""
    breaches = []
    constraints = strategy.constraints
    total_tasks = summary.get("total_tasks") or 0
    if constraints.get("max_outsource_percent") is not None and total_tasks:
        outsourced_percent = (summary.get("outsourced", 0) / total_tasks) * 100
        if outsourced_percent > constraints["max_outsource_percent"]:
            breaches.append(
                f"Outsourced {outsourced_percent:.1f}% of tasks, over the "
                f"{constraints['max_outsource_percent']:.1f}% limit.")
    min_util = constraints.get("min_utilisation_percent")
    if min_util is not None:
        avg_util = summary.get("avg_weight_utilisation")
        if avg_util is not None and avg_util < min_util:
            breaches.append(
                f"Average weight utilisation {avg_util:.1f}% is below the {min_util:.1f}% minimum.")
    return breaches
