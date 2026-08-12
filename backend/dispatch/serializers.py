from rest_framework import serializers

from .models import CarrierOffer, DispatchPlan, DispatchTask, HireRequirement, PlanEvent, PlannedRoute, PlannedStop, PlanVehicle


class PlanVehicleSerializer(serializers.ModelSerializer):
    registration_number = serializers.CharField(source="vehicle.registration_number", read_only=True, default="")
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")
    class Meta:
        model = PlanVehicle; fields = "__all__"


class DispatchTaskSerializer(serializers.ModelSerializer):
    pickup_name = serializers.CharField(source="pickup.name", read_only=True, default="")
    dropoff_name = serializers.CharField(source="dropoff.name", read_only=True, default="")
    order_number = serializers.CharField(source="order.number", read_only=True, default="")
    indent_number = serializers.CharField(source="indent.number", read_only=True, default="")
    class Meta:
        model = DispatchTask; fields = "__all__"


class PlannedStopSerializer(serializers.ModelSerializer):
    place_name = serializers.CharField(source="place.name", read_only=True, default="")
    task_detail = DispatchTaskSerializer(source="task", read_only=True)
    class Meta:
        model = PlannedStop; fields = "__all__"


class PlannedRouteSerializer(serializers.ModelSerializer):
    stops = PlannedStopSerializer(many=True, read_only=True)
    plan_vehicle_detail = PlanVehicleSerializer(source="plan_vehicle", read_only=True)
    gps_verified = serializers.SerializerMethodField()
    class Meta:
        model = PlannedRoute; fields = "__all__"

    def get_gps_verified(self, route):
        """Whether arrivals on this route can be trusted from a GPS device
        rather than a driver's own say-so - see docs/DISPATCH-PLANNING.md §7.3."""
        vehicle = route.plan_vehicle.vehicle
        return bool(vehicle and vehicle.gps_device_id)


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
