"""Per-vehicle cost basis for the planner, computed once per vehicle per solve -
not once per candidate, which was the bug fixed in `fleet/allocation.py`.

The one nuance worth stating: `cost_per_hour` carries only the reefer unit's
running cost, charged for dry vehicles as zero. A distance-only objective would
happily leave a loaded reefer idling for hours to hit a delivery window and call
it efficient; charging time for the classes that actually burn diesel while
idle is what keeps the plan's margin honest. See docs/DISPATCH-PLANNING.md §6.6.

**Lane-level spot pricing** (docs/DISPATCH-PLANNER-V2.md §7.1): `spot_rate_per_km`
used to be the only pricing signal - one national number, own cost x 1.15,
applied to every lane, every vehicle type, every season, ignoring the
business's own hire history entirely. `spot_rate_for_lane` replaces it with a
confidence-ranked lookup: a vendor's own negotiated `VendorLaneRate` contract
for this exact lane, then real `VehicleHire` history on this lane, then
history on the corridor (state to state), then the vendor average for this
vehicle type, and only then the flat fallback - so a plan can show whether an
outsource estimate is grounded or guessed, the same honesty
`fleet/allocation.py::_vendor_rate_estimate` already applies to a single order.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Avg, Q
from django.utils import timezone

from fleet.billing import running_cost
from fleet.models import VehicleHire, VendorLaneRate, money

DEFAULT_FIXED_COST = Decimal(str(getattr(settings, "DISPATCH_DEFAULT_FIXED_COST", "300")))
VENDOR_MARKUP = Decimal("1.15")
LANE_HISTORY_DAYS = 90

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
    basis for a task with no pre-arranged vehicle at all. The flat, no-history
    fallback tier of `spot_rate_for_lane`; kept as its own function since it is
    also the base every other confidence tier is a refinement of."""
    basis = vehicle_cost_basis(None)
    return money(basis["cost_per_km"] * VENDOR_MARKUP)


def _median(values):
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def _recent_hires():
    """Every km-basis `VehicleHire` from the last `LANE_HISTORY_DAYS`, loaded
    once per solve and filtered in Python per lane - cheaper than a fresh
    query for every distinct lane a plan's demand touches."""
    key = ("recent_hires",)
    if key in _cache:
        return _cache[key]
    since = timezone.now() - timedelta(days=LANE_HISTORY_DAYS)
    hires = list(VehicleHire.objects.filter(rate_basis="km", created_at__gte=since).exclude(status="cancelled")
                                    .select_related("order__pickup", "order__dropoff"))
    _cache[key] = hires
    return hires


def spot_rate_for_lane(pickup, dropoff, *, distance_km=None, vehicle_type="", temperature_class="dry"):
    """Returns `(rate_per_km, confidence)`. `confidence` is one of "contract",
    "lane", "corridor", "type" or "fallback", ranked in that order - the first
    tier with real data wins. `distance_km` is only used to convert a
    per-trip `VendorLaneRate` into a per-km figure; a per-km contract row
    needs it not at all."""
    cache_key = ("lane_rate", (pickup.city or "").lower(), (dropoff.city or "").lower(), vehicle_type.lower(), temperature_class)
    if cache_key in _cache:
        return _cache[cache_key]

    today = timezone.localdate()
    contract_qs = VendorLaneRate.objects.filter(
        origin_city__iexact=pickup.city, destination_city__iexact=dropoff.city, active=True,
        temperature_class=temperature_class,
    ).filter(Q(valid_from__isnull=True) | Q(valid_from__lte=today)) \
     .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=today))
    if vehicle_type:
        contract_qs = contract_qs.filter(Q(vehicle_type="") | Q(vehicle_type__iexact=vehicle_type))
    contract_rates = []
    for row in contract_qs:
        if row.rate_basis == "km":
            contract_rates.append(row.rate)
        elif row.rate_basis == "trip" and distance_km:
            contract_rates.append(row.rate / distance_km)
    contract_rate = _median(contract_rates)
    if contract_rate is not None:
        result = (money(contract_rate), "contract")
        _cache[cache_key] = result
        return result

    hires = _recent_hires()
    pickup_city, dropoff_city = (pickup.city or "").lower(), (dropoff.city or "").lower()
    lane_rate = _median([h.agreed_rate for h in hires
                         if (h.order.pickup.city or "").lower() == pickup_city
                         and (h.order.dropoff.city or "").lower() == dropoff_city])
    if lane_rate is not None:
        result = (money(lane_rate), "lane")
        _cache[cache_key] = result
        return result

    pickup_state, dropoff_state = (pickup.state or "").lower(), (dropoff.state or "").lower()
    corridor_rate = _median([h.agreed_rate for h in hires
                             if (h.order.pickup.state or "").lower() == pickup_state
                             and (h.order.dropoff.state or "").lower() == dropoff_state])
    if corridor_rate is not None:
        result = (money(corridor_rate), "corridor")
        _cache[cache_key] = result
        return result

    if vehicle_type:
        type_rate = _median([h.agreed_rate for h in hires if (h.outside_vehicle_type or "").lower() == vehicle_type.lower()])
        if type_rate is not None:
            result = (money(type_rate), "type")
            _cache[cache_key] = result
            return result

    result = (spot_rate_per_km(), "fallback")
    _cache[cache_key] = result
    return result
