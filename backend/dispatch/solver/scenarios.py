"""Scenario profiles: a dispatcher-configured planning logic + fallback per
operational pattern - milk run, long haul, reefer, local delivery, or any
custom profile - matched automatically per `Cluster` during a greedy solve.

This is deliberately independent of `strategies.py`, not a replacement for it:
`DispatchPlan.objective` is still the one strategy the whole solve runs under
(docs/DISPATCH-PLANNER-V2.md §3); a scenario profile only *narrows* it for the
clusters that match, the same way a photo filter sits on top of the base
image rather than repainting it. See docs/SCENARIO-PROFILES.md.
"""
from ..strategies import Strategy


def _matches(profile, cluster):
    """A cluster matches `profile` only when every criterion actually set on
    the profile is satisfied - an unset criterion matches anything."""
    if profile.match_temperature_classes and not (cluster.temperature_classes & set(profile.match_temperature_classes)):
        return False
    if profile.match_min_distance_km is not None:
        if cluster.distance_km is None or cluster.distance_km < profile.match_min_distance_km:
            return False
    if profile.match_max_distance_km is not None:
        if cluster.distance_km is None or cluster.distance_km > profile.match_max_distance_km:
            return False
    if profile.match_min_drops is not None and len(cluster.tasks) < profile.match_min_drops:
        return False
    if profile.match_same_city_only and not cluster.same_city:
        return False
    return True


def match_profile(cluster, profiles):
    """The first active profile, in priority order, whose criteria are all
    met by `cluster`. `profiles` should already be filtered to `active=True`
    and ordered by `priority` (the `ScenarioProfile` model's default
    ordering, so passing `ScenarioProfile.objects.filter(active=True)`
    straight through is enough)."""
    for profile in profiles:
        if _matches(profile, cluster):
            return profile
    return None


def effective_strategy(base_strategy, profile):
    """Merge `profile`'s weight/constraint overrides onto `base_strategy` -
    the plan's own strategy is still the foundation; a profile only nudges
    specific numbers, it never discards the rest of the vector."""
    if profile is None:
        return base_strategy
    weights = {**base_strategy.weights, **profile.weight_overrides}
    constraints = {**base_strategy.constraints, **profile.constraint_overrides}
    return Strategy(preset=base_strategy.preset, weights=weights, constraints=constraints)
