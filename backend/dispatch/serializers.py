from rest_framework import serializers

from .models import (CarrierOffer, DispatchPlan, DispatchTask, HireRequirement, PlanEvent, PlannedRoute, PlannedStop,
                     PlanVehicle, ScenarioProfile)
from .strategies import STRATEGY_PRESETS, StrategyError, resolve_strategy


class PlanVehicleSerializer(serializers.ModelSerializer):
    registration_number = serializers.CharField(source="vehicle.registration_number", read_only=True, default="")
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")
    class Meta:
        model = PlanVehicle; fields = "__all__"


class ScenarioProfileSerializer(serializers.ModelSerializer):
    fallback_profile_name = serializers.CharField(source="fallback_profile.name", read_only=True, default="")
    matched_task_count = serializers.IntegerField(source="matched_tasks.count", read_only=True)
    class Meta:
        model = ScenarioProfile; fields = "__all__"

    def validate_base_strategy(self, value):
        if value not in STRATEGY_PRESETS:
            raise serializers.ValidationError(f"Unknown strategy '{value}'. Choose one of: {', '.join(sorted(STRATEGY_PRESETS))}.")
        return value

    def validate(self, attrs):
        # Reuse the strategy resolver's own key/type validation rather than
        # duplicating it - a profile's overrides are checked exactly the way
        # a solve request's overrides are (strategies.resolve_strategy).
        weights = attrs.get("weight_overrides", getattr(self.instance, "weight_overrides", {}) or {})
        constraints = attrs.get("constraint_overrides", getattr(self.instance, "constraint_overrides", {}) or {})
        try:
            resolve_strategy({"weights": weights, "constraints": constraints})
        except StrategyError as error:
            raise serializers.ValidationError(str(error)) from error

        fallback_profile = attrs.get("fallback_profile", getattr(self.instance, "fallback_profile", None))
        if fallback_profile is not None and self.instance is not None and fallback_profile.pk == self.instance.pk:
            raise serializers.ValidationError("A profile cannot be its own fallback.")
        return attrs


class DispatchTaskSerializer(serializers.ModelSerializer):
    pickup_name = serializers.CharField(source="pickup.name", read_only=True, default="")
    dropoff_name = serializers.CharField(source="dropoff.name", read_only=True, default="")
    order_number = serializers.CharField(source="order.number", read_only=True, default="")
    indent_number = serializers.CharField(source="indent.number", read_only=True, default="")
    matched_scenario_name = serializers.CharField(source="matched_scenario.name", read_only=True, default="")
    class Meta:
        model = DispatchTask; fields = "__all__"


class PlannedStopSerializer(serializers.ModelSerializer):
    """Coordinates and order/customer, so the frontend can draw a route line
    and label a marker without a second round trip. See
    docs/DISPATCH-PLANNER-V2.md §6.1 - the map could not be built at all
    without this: the previous shape carried `place_name` but no coordinates."""
    place_name = serializers.CharField(source="place.name", read_only=True, default="")
    city = serializers.CharField(source="place.city", read_only=True, default="")
    latitude = serializers.DecimalField(source="place.latitude", max_digits=9, decimal_places=6, read_only=True, default=None)
    longitude = serializers.DecimalField(source="place.longitude", max_digits=9, decimal_places=6, read_only=True, default=None)
    order_number = serializers.CharField(source="task.order.number", read_only=True, default="")
    customer_name = serializers.CharField(source="task.order.customer.name", read_only=True, default="")
    task_detail = DispatchTaskSerializer(source="task", read_only=True)
    class Meta:
        model = PlannedStop; fields = "__all__"


class PlannedRouteSerializer(serializers.ModelSerializer):
    """KPI fields beyond the stored columns (`dead_km`, `utilisation_*_percent`,
    `estimated_*`) are derived here rather than stored, since every input they
    need - stops, the plan vehicle's capacity, each task's window - is already
    on the route. See docs/DISPATCH-PLANNER-V2.md §5.2."""
    stops = PlannedStopSerializer(many=True, read_only=True)
    plan_vehicle_detail = PlanVehicleSerializer(source="plan_vehicle", read_only=True)
    gps_verified = serializers.SerializerMethodField()
    laden_km = serializers.SerializerMethodField()
    dead_km_percent = serializers.SerializerMethodField()
    stop_count = serializers.SerializerMethodField()
    orders_carried = serializers.SerializerMethodField()
    revenue_per_km = serializers.SerializerMethodField()
    cost_per_tonne_km = serializers.SerializerMethodField()
    avg_utilisation_percent = serializers.SerializerMethodField()
    on_time_stops = serializers.SerializerMethodField()
    window_risk_stops = serializers.SerializerMethodField()
    path = serializers.SerializerMethodField()
    class Meta:
        model = PlannedRoute; fields = "__all__"

    def get_path(self, route):
        """`[[lat, lng], ...]` from the vehicle's start position through every
        stop in sequence, so the frontend draws a polyline without re-deriving
        it from separate stop coordinates."""
        pv = route.plan_vehicle
        path = []
        if pv and pv.start_latitude is not None and pv.start_longitude is not None:
            path.append([float(pv.start_latitude), float(pv.start_longitude)])
        for stop in route.stops.all():
            if stop.place.latitude is not None and stop.place.longitude is not None:
                path.append([float(stop.place.latitude), float(stop.place.longitude)])
        return path

    def get_gps_verified(self, route):
        """Whether arrivals on this route can be trusted from a GPS device
        rather than a driver's own say-so - see docs/DISPATCH-PLANNING.md §7.3."""
        vehicle = route.plan_vehicle.vehicle
        return bool(vehicle and vehicle.gps_device_id)

    def get_laden_km(self, route):
        if route.total_distance_km is None:
            return 0.0
        return float(route.total_distance_km - (route.dead_km or 0))

    def get_dead_km_percent(self, route):
        if not route.total_distance_km:
            return 0.0
        return float(round((route.dead_km or 0) / route.total_distance_km * 100, 1))

    def get_stop_count(self, route):
        return len(route.stops.all())

    def get_orders_carried(self, route):
        order_ids = {stop.task.order_id for stop in route.stops.all() if stop.task_id and stop.task.order_id}
        return len(order_ids)

    def get_revenue_per_km(self, route):
        if not route.total_distance_km:
            return None
        return float(round(route.estimated_revenue / route.total_distance_km, 2))

    def get_cost_per_tonne_km(self, route):
        tonnes = (route.max_load_kg or 0) / 1000
        if not tonnes or not route.total_distance_km:
            return None
        return float(round(route.estimated_cost / (tonnes * route.total_distance_km), 2))

    def get_avg_utilisation_percent(self, route):
        """The mean load across every leg, not just the peak - a truck full
        for one leg of eight is not a full truck (docs/DISPATCH-PLANNER-V2.md §5.2)."""
        capacity = route.plan_vehicle.capacity_kg if route.plan_vehicle_id else None
        stops = list(route.stops.all())
        if not capacity or not stops:
            return None
        average_load = sum((stop.load_after_kg for stop in stops), 0) / len(stops)
        return float(round(min(100, average_load / capacity * 100), 1))

    def _windowed_stops(self, route):
        return [stop for stop in route.stops.all() if stop.task_id and stop.task.drop_window_end and stop.planned_arrival]

    def get_on_time_stops(self, route):
        windowed = self._windowed_stops(route)
        if not windowed:
            return None
        on_time = sum(1 for stop in windowed if stop.planned_arrival <= stop.task.drop_window_end)
        return {"on_time": on_time, "total": len(windowed)}

    def get_window_risk_stops(self, route):
        """Stops arriving within 30 minutes of their deadline - close enough
        that a small delay tips them late."""
        at_risk = 0
        for stop in self._windowed_stops(route):
            if stop.planned_arrival > stop.task.drop_window_end:
                continue
            minutes_to_spare = (stop.task.drop_window_end - stop.planned_arrival).total_seconds() / 60
            if minutes_to_spare <= 30:
                at_risk += 1
        return at_risk


class PlanEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanEvent; fields = "__all__"


class CarrierOfferSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")
    class Meta:
        model = CarrierOffer; fields = "__all__"


class HireRequirementSerializer(serializers.ModelSerializer):
    pickup_name = serializers.CharField(source="pickup.name", read_only=True, default="")
    dropoff_name = serializers.CharField(source="dropoff.name", read_only=True, default="")
    task_count = serializers.IntegerField(source="tasks.count", read_only=True)
    offers = CarrierOfferSerializer(many=True, read_only=True)
    class Meta:
        model = HireRequirement; fields = "__all__"


class DispatchPlanSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True, default="")
    class Meta:
        model = DispatchPlan; fields = "__all__"
        read_only_fields = ["code", "status", "solver_seconds", "solver_status", "summary", "committed_at", "committed_by"]


class DispatchPlanDetailSerializer(DispatchPlanSerializer):
    routes = PlannedRouteSerializer(many=True, read_only=True)
    tasks = DispatchTaskSerializer(many=True, read_only=True)
    hire_requirements = HireRequirementSerializer(many=True, read_only=True)
    events = PlanEventSerializer(many=True, read_only=True)
    plan_vehicles = PlanVehicleSerializer(many=True, read_only=True)
