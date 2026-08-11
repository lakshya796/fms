"""Distance and travel time between two points, cached in `TravelMatrixEntry`.

Only the `haversine` provider is implemented here - a straight-line distance
scaled by a configurable detour factor, which is what every environment has on
day one with no external dependency. `osrm` / `google` / `learned` providers are
the Phase E/F upgrade path described in docs/DISPATCH-PLANNING.md §6.9; the
cache and the call signature are already shaped for them to slot in without
touching the solver.
"""
from decimal import Decimal

from django.conf import settings
from django.db.models import F

from fleet.models import haversine_km

from ..models import TravelMatrixEntry

DETOUR_FACTOR = Decimal(str(getattr(settings, "DISPATCH_DETOUR_FACTOR", "1.35")))
DEFAULT_SPEED_KPH = Decimal(str(getattr(settings, "DISPATCH_DEFAULT_SPEED_KPH", "40")))


def _key(lat, lng):
    return f"{float(lat):.4f},{float(lng):.4f}"


def distance_and_duration(origin, destination, *, average_speed_kph=None, provider="haversine"):
    """`origin`/`destination` are (lat, lng) pairs (Decimal, float or None).

    Returns (distance_km, duration_minutes) as Decimal, or (None, None) when
    either point lacks coordinates - the caller's job to treat that as "cannot
    route this", not to guess a distance.
    """
    if origin is None or destination is None or origin[0] is None or origin[1] is None \
       or destination[0] is None or destination[1] is None:
        return None, None
    okey, dkey = _key(*origin), _key(*destination)
    if okey == dkey:
        return Decimal("0"), Decimal("0")
    speed = Decimal(str(average_speed_kph)) if average_speed_kph else DEFAULT_SPEED_KPH

    entry = TravelMatrixEntry.objects.filter(
        origin_key=okey, destination_key=dkey, provider=provider, vehicle_class="", time_bucket="").first()
    if entry:
        TravelMatrixEntry.objects.filter(pk=entry.pk).update(hit_count=F("hit_count") + 1)
        return entry.distance_km, entry.duration_minutes

    straight = Decimal(str(haversine_km(float(origin[0]), float(origin[1]), float(destination[0]), float(destination[1]))))
    distance = (straight * DETOUR_FACTOR).quantize(Decimal("0.01"))
    duration = (distance / speed * 60).quantize(Decimal("0.1"))
    TravelMatrixEntry.objects.update_or_create(
        origin_key=okey, destination_key=dkey, provider=provider, vehicle_class="", time_bucket="",
        defaults={"distance_km": distance, "duration_minutes": duration, "hit_count": 1})
    return distance, duration
