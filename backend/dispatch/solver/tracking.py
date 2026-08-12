"""GPS in the loop: turning a vehicle's live position (or a driver's check-in,
for a hired truck with no device) into confirmed stop arrivals, feeding the
learned travel-time cache, and finding routes that have drifted enough to need
a re-plan. See docs/DISPATCH-PLANNING.md §7.
"""
from decimal import Decimal

from django.utils import timezone

from fleet.models import haversine_km

from . import matrix

GEOFENCE_RADIUS_KM = Decimal(str(2.0))


def mark_arrived(stop, *, at=None):
    """Idempotent: a stop already marked arrived is left alone, so a late
    duplicate geofence hit or a driver's second tap does not overwrite the
    first, truer, timestamp."""
    if stop.actual_arrival:
        return stop
    at = at or timezone.now()
    stop.actual_arrival = at
    if stop.planned_arrival:
        stop.variance_minutes = int((at - stop.planned_arrival).total_seconds() // 60)
    stop.save(update_fields=["actual_arrival", "variance_minutes", "updated_at"])
    return stop


def mark_departed(stop, *, at=None):
    if stop.actual_departure:
        return stop
    at = at or timezone.now()
    if not stop.actual_arrival:
        mark_arrived(stop, at=at)
    stop.actual_departure = at
    stop.save(update_fields=["actual_departure", "updated_at"])
    _write_back_leg(stop)
    return stop


def _write_back_leg(stop):
    """The leg into `stop` from the previous confirmed stop on the same route -
    the duration is genuinely observed; the distance stays whatever the system
    already believed for the pair (learned, if one already exists, else the
    geometric estimate), since a place-to-place timestamp alone does not measure
    road distance."""
    from ..models import PlannedStop
    previous = PlannedStop.objects.filter(route_id=stop.route_id, sequence__lt=stop.sequence,
                                          actual_departure__isnull=False).order_by("-sequence").first()
    if previous is None or stop.actual_arrival is None:
        return
    if previous.place.latitude is None or previous.place.longitude is None \
       or stop.place.latitude is None or stop.place.longitude is None:
        return
    actual_minutes = Decimal(str((stop.actual_arrival - previous.actual_departure).total_seconds() / 60))
    if actual_minutes <= 0:
        return
    origin = (previous.place.latitude, previous.place.longitude)
    destination = (stop.place.latitude, stop.place.longitude)
    known_distance, _ = matrix.distance_and_duration(origin, destination, depart_at=previous.actual_departure)
    matrix.record_learned_leg(origin, destination, distance_km=known_distance, duration_minutes=actual_minutes,
                              at=previous.actual_departure)


def auto_capture_arrivals(plan=None, radius_km=GEOFENCE_RADIUS_KM):
    """Compare each committed route's next un-arrived stop against its
    vehicle's live GPS position; mark it arrived when within `radius_km`.

    Only the *next* stop on each route is ever checked - a route is processed
    in sequence, so a match anywhere else would mean either bad data or a
    manual override, neither of which this should paper over automatically.
    A vehicle with no `gps_device_id` never matches here; its route relies on
    the manual `PlannedStopViewSet.arrive`/`depart` (driver check-in) instead.
    """
    from ..models import PlannedRoute
    routes = PlannedRoute.objects.filter(committed_trip__isnull=False).select_related("plan_vehicle__vehicle")
    if plan is not None:
        routes = routes.filter(plan=plan)
    captured = []
    for route in routes:
        vehicle = route.plan_vehicle.vehicle
        if vehicle is None or not vehicle.gps_device_id or vehicle.current_latitude is None or vehicle.current_longitude is None:
            continue
        next_stop = route.stops.filter(actual_arrival__isnull=True).order_by("sequence").first()
        if next_stop is None or next_stop.place.latitude is None or next_stop.place.longitude is None:
            continue
        distance = Decimal(str(haversine_km(float(vehicle.current_latitude), float(vehicle.current_longitude),
                                            float(next_stop.place.latitude), float(next_stop.place.longitude))))
        if distance <= radius_km:
            mark_arrived(next_stop)
            captured.append(next_stop)
    return captured


def routes_needing_replan(plan, threshold_minutes=60):
    """Committed routes running more than `threshold_minutes` behind their own
    plan, from the last stop with a confirmed arrival - the re-plan trigger
    described in §7.3, made queryable rather than left to a dispatcher noticing."""
    behind = []
    for route in plan.routes.filter(committed_trip__isnull=False).select_related("plan_vehicle__vehicle"):
        last_arrived = route.stops.filter(actual_arrival__isnull=False).order_by("-sequence").first()
        if last_arrived is None or last_arrived.variance_minutes is None:
            continue
        if last_arrived.variance_minutes >= threshold_minutes:
            behind.append({"route": route.id, "vehicle": getattr(route.plan_vehicle.vehicle, "registration_number", None),
                           "behind_by_minutes": last_arrived.variance_minutes})
    return behind
