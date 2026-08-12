"""Distance and travel time between two points, cached in `TravelMatrixEntry`.

The `haversine` provider - a straight-line distance scaled by a configurable
detour factor - is what every environment has on day one with no external
dependency. `osrm` / `google` providers are the Phase F upgrade path described
in docs/DISPATCH-PLANNING.md §6.9; the cache and the call signature are already
shaped for them to slot in without touching the solver.

`learned` entries (§7.3) come from actual GPS-confirmed arrivals
(`views.PlannedStopViewSet.depart`) rather than any external call, and are
preferred over a fresh geometric estimate whenever one exists for the pair and
time-of-day band - a route through Mumbai at 18:00 should use what actually
happened last time, not a flat detour factor.
"""
from decimal import Decimal

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from fleet.models import haversine_km

from ..models import TravelMatrixEntry

DETOUR_FACTOR = Decimal(str(getattr(settings, "DISPATCH_DETOUR_FACTOR", "1.35")))
DEFAULT_SPEED_KPH = Decimal(str(getattr(settings, "DISPATCH_DEFAULT_SPEED_KPH", "40")))
TIME_BUCKET_HOURS = 2


def _key(lat, lng):
    return f"{float(lat):.4f},{float(lng):.4f}"


def time_bucket_for(at):
    """A 2-hour band, e.g. '08-10' - fine enough to separate rush hour from
    midnight without fragmenting the learned cache into one row per minute."""
    at = timezone.localtime(at) if timezone.is_aware(at) else at
    start = (at.hour // TIME_BUCKET_HOURS) * TIME_BUCKET_HOURS
    return f"{start:02d}-{(start + TIME_BUCKET_HOURS) % 24:02d}"


def _learned_entry(okey, dkey, time_bucket=None):
    queryset = TravelMatrixEntry.objects.filter(origin_key=okey, destination_key=dkey, provider="learned", vehicle_class="")
    if time_bucket:
        matching = queryset.filter(time_bucket=time_bucket).first()
        if matching:
            return matching
    return queryset.order_by("-hit_count").first()


def distance_and_duration(origin, destination, *, average_speed_kph=None, provider="haversine", depart_at=None):
    """`origin`/`destination` are (lat, lng) pairs (Decimal, float or None).

    Returns (distance_km, duration_minutes) as Decimal, or (None, None) when
    either point lacks coordinates - the caller's job to treat that as "cannot
    route this", not to guess a distance. A learned entry for the pair (and,
    where `depart_at` is given, the matching time-of-day band) is used ahead of
    the requested `provider` whenever one exists.
    """
    if origin is None or destination is None or origin[0] is None or origin[1] is None \
       or destination[0] is None or destination[1] is None:
        return None, None
    okey, dkey = _key(*origin), _key(*destination)
    if okey == dkey:
        return Decimal("0"), Decimal("0")

    if provider != "learned":
        bucket = time_bucket_for(depart_at) if depart_at else None
        learned = _learned_entry(okey, dkey, bucket)
        if learned:
            TravelMatrixEntry.objects.filter(pk=learned.pk).update(hit_count=F("hit_count") + 1)
            return learned.distance_km, learned.duration_minutes

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


def record_learned_leg(origin, destination, *, distance_km, duration_minutes, at):
    """Write back one GPS-confirmed leg - a running average per (pair, time
    bucket) rather than an overwrite, so one unusually slow or fast run does not
    swing the estimate the whole way.
    """
    if origin is None or destination is None or origin[0] is None or origin[1] is None \
       or destination[0] is None or destination[1] is None or distance_km is None or duration_minutes is None:
        return None
    okey, dkey = _key(*origin), _key(*destination)
    if okey == dkey:
        return None
    bucket = time_bucket_for(at)
    entry = TravelMatrixEntry.objects.filter(
        origin_key=okey, destination_key=dkey, provider="learned", vehicle_class="", time_bucket=bucket).first()
    if entry:
        weight = entry.hit_count or 1
        blended_distance = (entry.distance_km * weight + Decimal(str(distance_km))) / (weight + 1)
        blended_duration = (entry.duration_minutes * weight + Decimal(str(duration_minutes))) / (weight + 1)
        entry.distance_km = blended_distance.quantize(Decimal("0.01"))
        entry.duration_minutes = blended_duration.quantize(Decimal("0.1"))
        entry.hit_count = weight + 1
        entry.fetched_at = at
        entry.save(update_fields=["distance_km", "duration_minutes", "hit_count", "fetched_at", "updated_at"])
        return entry
    return TravelMatrixEntry.objects.create(
        origin_key=okey, destination_key=dkey, provider="learned", vehicle_class="", time_bucket=bucket,
        distance_km=Decimal(str(distance_km)).quantize(Decimal("0.01")),
        duration_minutes=Decimal(str(duration_minutes)).quantize(Decimal("0.1")),
        fetched_at=at, hit_count=1)
