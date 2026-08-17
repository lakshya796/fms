"""Seed the four scenario profiles named in the product ask - milk run, long
haul, reefer, local delivery - with sensible starting criteria and fallback
logic a dispatcher can then tune from the Scenario Profiles screen.

Usage: python manage.py seed_scenario_profiles

Idempotent - re-running it updates the same four profiles (matched by name)
rather than duplicating them, so it is safe to run more than once and safe to
run in production after a dispatcher has already edited one (their edits to
any *other* field are untouched; only the fields listed here are reset).
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from dispatch.models import ScenarioProfile

PROFILES = [
    {
        "name": "Milk Run", "scenario_type": "milk_run", "priority": 10,
        "description": "Several drops consolidated onto one pickup - reward filling one truck "
                       "over starting a second.",
        "match_min_drops": 2,
        "base_strategy": "max_utilisation",
        "weight_overrides": {"utilisation_bonus": 50.0, "fixed_cost": 1.5},
        "constraint_overrides": {},
        "fallback_action": "outsource",
    },
    {
        "name": "Long Haul", "scenario_type": "long_haul", "priority": 20,
        "description": "Interstate or otherwise high-distance runs - a little more weight on "
                       "driver time, a longer duty allowance for a relay, and pushed to the "
                       "next plan rather than forced through when it does not fit today.",
        "match_min_distance_km": Decimal("400.0"),
        "base_strategy": "balanced",
        "weight_overrides": {"time_cost": 1.3},
        "constraint_overrides": {"max_duty_minutes": 720},
        "fallback_action": "defer",
    },
    {
        "name": "Reefer", "scenario_type": "reefer", "priority": 5,
        "description": "Chiller or frozen cargo - delivery windows are hard constraints, not "
                       "suggestions, and a load that cannot be placed is held for a human to "
                       "check rather than bought from an unverified market vendor.",
        "match_temperature_classes": ["chiller", "frozen"],
        "base_strategy": "balanced",
        "weight_overrides": {"window_miss_penalty": 2000.0},
        "constraint_overrides": {"time_windows": "hard"},
        "fallback_action": "hold",
    },
    {
        "name": "Local Delivery", "scenario_type": "local_delivery", "priority": 30,
        "description": "Short, same-city runs - cost-driven, and always fine to buy on the "
                       "market when that is cheaper.",
        "match_max_distance_km": Decimal("50.0"), "match_same_city_only": True,
        "base_strategy": "least_cost",
        "weight_overrides": {},
        "constraint_overrides": {},
        "fallback_action": "outsource",
    },
]


class Command(BaseCommand):
    help = "Seed the Milk Run, Long Haul, Reefer and Local Delivery scenario profiles."

    def handle(self, *args, **options):
        created, updated = 0, 0
        for spec in PROFILES:
            name = spec.pop("name")
            _, was_created = ScenarioProfile.objects.update_or_create(name=name, defaults=spec)
            created += was_created
            updated += not was_created
            spec["name"] = name   # restore, in case PROFILES is reused (e.g. under test)
        self.stdout.write(self.style.SUCCESS(
            f"Scenario profiles: {created} created, {updated} updated."))
