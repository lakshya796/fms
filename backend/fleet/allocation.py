"""Vehicle recommendation: given an order, which vehicle should carry it.

Deterministic on purpose - dead km, fuel, toll and expected profit, not a model.
The spec is explicit that the answer should be the best practical and profitable
vehicle, not simply the nearest one, so every candidate here carries a cost and a
revenue, and the ranking is by expected profit rather than distance.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg
from django.utils import timezone

from .billing import running_cost
from .models import Vehicle, Vendor, VehicleHire, haversine_km, money

FREE_VEHICLE_STATUSES = ("available", "idle")
# A vehicle mid-trip is still a candidate if it will be free before the order needs
# it - these are the statuses `Vehicle.expected_available_at` is meaningful for.
SOON_FREE_STATUSES = ("running", "loaded", "unloaded", "allocated", "awaiting_unloading", "on_trip")
DEFAULT_VENDOR_MARKUP = Decimal("1.15")  # assumed vendor margin over our own running cost, used only as a last resort


class _CostCache:
    """One `running_cost()` per vehicle rather than one per candidate.

    `recommend_vehicles` used to call `running_cost(vehicle=vehicle)` inside the
    candidate loop - two aggregate queries per vehicle, which is 2000 queries
    before the ranking even starts on a 1000-vehicle fleet. The basis only depends
    on the vehicle (or nothing, for the fleet-wide fallback), so it is computed
    once per key and reused.
    """
    def __init__(self):
        self._cache = {}

    def get(self, vehicle=None):
        key = vehicle.pk if vehicle is not None else None
        if key not in self._cache:
            self._cache[key] = running_cost(vehicle=vehicle)
        return self._cache[key]


def _dead_km(vehicle, pickup):
    if vehicle.current_latitude is None or vehicle.current_longitude is None \
       or pickup.latitude is None or pickup.longitude is None:
        return None
    return Decimal(str(haversine_km(vehicle.current_latitude, vehicle.current_longitude,
                                    pickup.latitude, pickup.longitude)))


def _expected_revenue(order):
    if order.total_amount:
        return money(order.total_amount)
    if order.service_rate:
        return money(order.service_rate.quote(distance_km=order.distance_km, weight_kg=order.weight_kg)["total"])
    return Decimal("0")


def _own_candidate(vehicle, order, revenue, cost_cache):
    dead_km = _dead_km(vehicle, order.pickup)
    laden_km = Decimal(str(order.distance_km or 0))
    total_km = (dead_km or Decimal("0")) + laden_km
    basis = cost_cache.get(vehicle)
    cost = money(basis["fuel_cost_per_km"] * total_km + basis["on_road_cost_per_km"] * total_km)
    return {
        "source": vehicle.ownership, "vehicle_id": vehicle.pk, "registration_number": vehicle.registration_number,
        "vehicle_type": vehicle.vehicle_type, "vendor_id": vehicle.vendor_id, "vendor_name": getattr(vehicle.vendor, "name", ""),
        "dead_km": float(dead_km) if dead_km is not None else None, "laden_km": float(laden_km),
        "expected_cost": float(cost), "cost_basis": "own running cost", "estimated_cost": False,
        "expected_revenue": float(revenue), "expected_profit": float(money(revenue - cost)),
        "fit_notes": _fit_notes(vehicle, order),
        "available_now": vehicle.status in FREE_VEHICLE_STATUSES,
        "expected_available_at": vehicle.expected_available_at,
    }


def _vendor_rate_estimate(vendor, order, cost_cache):
    """What this vendor is likely to charge, from their own hire history where it
    exists, or a markup over our own running cost as a last-resort estimate.
    """
    laden_km = Decimal(str(order.distance_km or 0))
    per_km_avg = VehicleHire.objects.filter(vendor=vendor, rate_basis="km").exclude(status="cancelled") \
                                    .aggregate(value=Avg("agreed_rate"))["value"]
    if per_km_avg:
        return money(Decimal(str(per_km_avg)) * laden_km), False
    per_trip_avg = VehicleHire.objects.filter(vendor=vendor, rate_basis="trip").exclude(status="cancelled") \
                                      .aggregate(value=Avg("agreed_rate"))["value"]
    if per_trip_avg:
        return money(per_trip_avg), False
    basis = cost_cache.get(None)
    return money((basis["fuel_cost_per_km"] + basis["on_road_cost_per_km"]) * laden_km * DEFAULT_VENDOR_MARKUP), True


def _vendor_vehicle_candidate(vehicle, order, revenue, cost_cache):
    cost, estimated = _vendor_rate_estimate(vehicle.vendor, order, cost_cache)
    dead_km = _dead_km(vehicle, order.pickup)
    return {
        "source": vehicle.ownership, "vehicle_id": vehicle.pk, "registration_number": vehicle.registration_number,
        "vehicle_type": vehicle.vehicle_type, "vendor_id": vehicle.vendor_id, "vendor_name": vehicle.vendor.name,
        "dead_km": float(dead_km) if dead_km is not None else None, "laden_km": float(order.distance_km or 0),
        "expected_cost": float(cost), "cost_basis": "vendor hire history" if not estimated else "estimated from own cost + markup",
        "estimated_cost": estimated, "expected_revenue": float(revenue), "expected_profit": float(money(revenue - cost)),
        "fit_notes": _fit_notes(vehicle, order),
        "available_now": vehicle.status in FREE_VEHICLE_STATUSES,
        "expected_available_at": vehicle.expected_available_at,
    }


def _spot_vendor_candidate(vendor, order, revenue, cost_cache):
    cost, estimated = _vendor_rate_estimate(vendor, order, cost_cache)
    return {
        "source": "vendor_spot", "vehicle_id": None, "registration_number": None, "vehicle_type": "",
        "vendor_id": vendor.pk, "vendor_name": vendor.name, "dead_km": None, "laden_km": float(order.distance_km or 0),
        "expected_cost": float(cost), "cost_basis": "vendor hire history" if not estimated else "estimated from own cost + markup",
        "estimated_cost": estimated, "expected_revenue": float(revenue), "expected_profit": float(money(revenue - cost)),
        "fit_notes": ["No specific vehicle on file for this vendor yet - a spot-hire estimate."],
    }


def _fit_notes(vehicle, order):
    notes = []
    if order.weight_kg and vehicle.capacity_kg and vehicle.capacity_kg < order.weight_kg:
        notes.append(f"Capacity {vehicle.capacity_kg} kg is short of the {order.weight_kg} kg consignment.")
    return notes


def recommend_vehicles(order, *, max_dead_km=None, include_vendor=True, limit=10):
    """Ranked vehicle candidates for `order`, own capacity first, then vendor
    capacity, sorted by expected profit - the comparison table the spec describes.
    """
    revenue = _expected_revenue(order)
    candidates = []
    cost_cache = _CostCache()

    # A vehicle that is free now, or one that will be free before this order's
    # required time, is a real candidate - excluding the latter means a
    # day-ahead order can only ever see today's fleet, not tomorrow's.
    needed_by = order.scheduled_at or (timezone.now() + timedelta(hours=6))
    own_qs = Vehicle.objects.exclude(ownership="outside") \
                            .filter(status__in=FREE_VEHICLE_STATUSES) \
                            .select_related("vendor")
    soon_free_qs = Vehicle.objects.exclude(ownership="outside") \
                                  .filter(status__in=SOON_FREE_STATUSES, expected_available_at__isnull=False,
                                          expected_available_at__lte=needed_by) \
                                  .select_related("vendor")
    for vehicle in list(own_qs) + list(soon_free_qs):
        candidates.append(_own_candidate(vehicle, order, revenue, cost_cache))

    if include_vendor:
        vendor_vehicle_qs = Vehicle.objects.filter(ownership__in=["attached", "outside"], vendor__isnull=False,
                                                    status__in=FREE_VEHICLE_STATUSES).select_related("vendor")
        vendors_with_vehicles = set()
        for vehicle in vendor_vehicle_qs:
            candidates.append(_vendor_vehicle_candidate(vehicle, order, revenue, cost_cache))
            vendors_with_vehicles.add(vehicle.vendor_id)
        for vendor in Vendor.objects.filter(vendor_type__in=["transporter", "broker"], status="active") \
                                    .exclude(pk__in=vendors_with_vehicles):
            candidates.append(_spot_vendor_candidate(vendor, order, revenue, cost_cache))

    if max_dead_km is not None:
        candidates = [c for c in candidates if c["dead_km"] is None or c["dead_km"] <= float(max_dead_km)]

    candidates.sort(key=lambda c: c["expected_profit"], reverse=True)
    for rank, candidate in enumerate(candidates[:limit], start=1):
        candidate["rank"] = rank
        candidate["recommended"] = rank == 1
    return candidates[:limit]
