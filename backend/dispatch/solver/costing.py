"""Per-vehicle cost basis for the planner, computed once per vehicle per solve -
not once per candidate, which was the bug fixed in `fleet/allocation.py`.

The one nuance worth stating: `cost_per_hour` carries only the reefer unit's
running cost, charged for dry vehicles as zero. A distance-only objective would
happily leave a loaded reefer idling for hours to hit a delivery window and call
it efficient; charging time for the classes that actually burn diesel while
idle is what keeps the plan's margin honest. See docs/DISPATCH-PLANNING.md §6.6.
"""
from decimal import Decimal

from django.conf import settings
from django.db.models import Avg

from fleet.billing import running_cost
from fleet.models import VehicleHire, money

DEFAULT_FIXED_COST = Decimal(str(getattr(settings, "DISPATCH_DEFAULT_FIXED_COST", "300")))
VENDOR_MARKUP = Decimal("1.15")

_cache = {}


def reset_cache():
    """Called once at the start of a solve - the basis is only stable for the
    lifetime of one run, not across an app server's whole lifetime."""
    _cache.clear()


def vehicle_cost_basis(vehicle):
    """cost_per_km / cost_per_hour / fixed_cost for an owned or attached vehicle,
    from this fleet's own recorded diesel and on-road spend."""
    key = ("own", vehicle.pk if vehicle else None)
    if key in _cache:
        return _cache[key]
    basis = running_cost(vehicle=vehicle)
    cost_per_km = money(basis["fuel_cost_per_km"] + basis["on_road_cost_per_km"])
    reefer_hourly = Decimal("0")
    temp_class = getattr(vehicle, "temperature_class", "dry")
    reefer_lph = getattr(vehicle, "reefer_fuel_lph", 0) or 0
    if temp_class in ("chiller", "frozen", "multi") and reefer_lph:
        reefer_hourly = money(Decimal(str(reefer_lph)) * basis["diesel_price"])
    result = {"cost_per_km": cost_per_km, "cost_per_hour": reefer_hourly, "fixed_cost": DEFAULT_FIXED_COST,
             "diesel_price": basis["diesel_price"], "estimated": False}
    _cache[key] = result
    return result


def vendor_cost_basis(vehicle):
    """cost_per_km for an attached vendor's own truck: their own hire history
    where it exists, a markup over our running cost where it does not - the same
    fallback `fleet/allocation.py::_vendor_rate_estimate` uses for a single order."""
    key = ("vendor", vehicle.vendor_id)
    if key in _cache:
        return _cache[key]
    per_km_avg = VehicleHire.objects.filter(vendor_id=vehicle.vendor_id, rate_basis="km") \
                                    .exclude(status="cancelled").aggregate(value=Avg("agreed_rate"))["value"]
    if per_km_avg:
        result = {"cost_per_km": money(Decimal(str(per_km_avg))), "cost_per_hour": Decimal("0"),
                  "fixed_cost": Decimal("0"), "diesel_price": running_cost()["diesel_price"], "estimated": False}
    else:
        own = vehicle_cost_basis(None)
        result = {"cost_per_km": money(own["cost_per_km"] * VENDOR_MARKUP), "cost_per_hour": Decimal("0"),
                  "fixed_cost": Decimal("0"), "diesel_price": own["diesel_price"], "estimated": True}
    _cache[key] = result
    return result


def spot_rate_per_km():
    """What the open market would charge per laden km - the disjunction penalty
    basis for a task with no pre-arranged vehicle at all."""
    basis = vehicle_cost_basis(None)
    return money(basis["cost_per_km"] * VENDOR_MARKUP)
