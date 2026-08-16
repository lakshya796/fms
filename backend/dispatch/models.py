"""Dispatch planning: day-ahead multi-order routing across own dry vehicles, own
reefers and third-party hire.

This app only reads `fleet` (vehicles, orders, indents, places) and writes plans;
the dependency is one-directional on purpose, so the 1000-line `fleet/models.py`
does not grow another third and a plan can be re-derived or thrown away without
touching operational data until it is committed (see `views.commit`).

See docs/DISPATCH-PLANNING.md for the design this implements.
"""
from decimal import Decimal
from uuid import uuid4

from django.db import models
from django.utils import timezone


class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True


PLAN_STATUSES = [("draft", "Draft"), ("ready", "Ready to solve"), ("solving", "Solving"),
                 ("solved", "Solved"), ("failed", "Failed"), ("committed", "Committed"),
                 ("superseded", "Superseded by a re-plan")]

SOLVERS = [("greedy", "Greedy (savings + local search)"), ("ortools", "OR-Tools")]


class DispatchPlan(Timestamped):
    """One planning run for a branch and a date/time horizon."""
    code = models.CharField(max_length=30, unique=True)
    branch = models.ForeignKey("iam.Branch", on_delete=models.SET_NULL, null=True, blank=True, related_name="dispatch_plans")
    plan_date = models.DateField(default=timezone.localdate)
    horizon_hours = models.PositiveIntegerField(default=24)
    status = models.CharField(max_length=12, choices=PLAN_STATUSES, default="draft")
    objective = models.JSONField(default=dict, blank=True, help_text="Cost/service weight overrides for the solver")
    collection_filters = models.JSONField(default=dict, blank=True,
                                          help_text="What universe of demand `collect` pulled in - customers, "
                                                    "places, temperature class, a date window or explicit order ids")
    solver = models.CharField(max_length=10, choices=SOLVERS, default="greedy")
    solver_seconds = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    solver_status = models.CharField(max_length=40, blank=True)
    parent_plan = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replans")
    is_scenario = models.BooleanField(default=False,
                                      help_text="A what-if child plan from `compare` - not shown as a first-class "
                                                "plan until `adopt` transplants its routes onto the parent")
    summary = models.JSONField(default=dict, blank=True)
    created_by = models.CharField(max_length=150, blank=True)
    committed_at = models.DateTimeField(null=True, blank=True)
    committed_by = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ["-plan_date", "-created_at"]
        indexes = [models.Index(fields=["status", "-plan_date"])]

    def save(self, *args, **kwargs):
        if not self.code:
            # A random suffix, not just a timestamp: two plans created in the same
            # second (routine for a re-plan, which creates a child right after its
            # parent) would otherwise collide on this unique field.
            self.code = "DP-" + timezone.now().strftime("%y%m%d") + "-" + uuid4().hex[:6].upper()
        super().save(*args, **kwargs)

    def log(self, event_type, description="", data=None):
        return PlanEvent.objects.create(plan=self, event_type=event_type, description=description, data=data or {})

    def __str__(self):
        return self.code


TASK_TYPES = [("ftl", "Full truckload"), ("multi_drop_leg", "Multi-drop leg"),
             ("pickup_only", "Pickup only"), ("delivery_only", "Delivery only"), ("reposition", "Repositioning")]
TEMPERATURE_CLASSES = [("dry", "Dry"), ("chiller", "Chiller"), ("frozen", "Frozen")]
TASK_PRIORITIES = [("must_go", "Must go - cannot be outsourced away"), ("normal", "Normal"), ("deferrable", "Deferrable")]
OUTSOURCE_CONFIDENCE = [("contract", "Vendor contract rate"), ("lane", "This lane's own history"),
                        ("corridor", "Corridor history (state to state)"), ("type", "Vendor's vehicle-type average"),
                        ("fallback", "National fallback - no history")]
TASK_STATUSES = [("pending", "Pending"), ("planned", "Planned"), ("outsourced", "Outsourced"),
                 ("dropped", "Dropped"), ("committed", "Committed")]


class DispatchTask(Timestamped):
    """One unit of demand handed to the solver, derived from an Order or an Indent."""
    plan = models.ForeignKey(DispatchPlan, on_delete=models.CASCADE, related_name="tasks")
    order = models.ForeignKey("fleet.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="dispatch_tasks")
    indent = models.ForeignKey("fleet.Indent", on_delete=models.SET_NULL, null=True, blank=True, related_name="dispatch_tasks")
    task_type = models.CharField(max_length=20, choices=TASK_TYPES, default="ftl")
    pickup = models.ForeignKey("fleet.Place", on_delete=models.PROTECT, related_name="dispatch_pickups")
    dropoff = models.ForeignKey("fleet.Place", on_delete=models.PROTECT, related_name="dispatch_dropoffs")
    weight_kg = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    volume_cbm = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    packages = models.PositiveIntegerField(default=1)
    temperature_class = models.CharField(max_length=10, choices=TEMPERATURE_CLASSES, default="dry")
    temp_set_point_c = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    pickup_window_start = models.DateTimeField(null=True, blank=True)
    pickup_window_end = models.DateTimeField(null=True, blank=True)
    drop_window_start = models.DateTimeField(null=True, blank=True)
    drop_window_end = models.DateTimeField(null=True, blank=True)
    pickup_service_minutes = models.PositiveIntegerField(default=30)
    drop_service_minutes = models.PositiveIntegerField(default=30)
    priority = models.CharField(max_length=12, choices=TASK_PRIORITIES, default="normal")
    allowed_vehicle_types = models.JSONField(default=list, blank=True)
    banned_vehicles = models.JSONField(default=list, blank=True)
    revenue_estimate = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    outsource_estimate = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                             help_text="What the spot market would charge to move this - the disjunction penalty")
    outsource_confidence = models.CharField(max_length=10, choices=OUTSOURCE_CONFIDENCE, default="fallback",
                                            help_text="How grounded outsource_estimate is - see solver.costing.spot_rate_for_lane")
    status = models.CharField(max_length=12, choices=TASK_STATUSES, default="pending")
    drop_reason = models.CharField(max_length=240, blank=True)
    pinned_vehicle = models.ForeignKey("fleet.Vehicle", on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="pinned_dispatch_tasks",
                                       help_text="A dispatcher-forced vehicle for this task - honoured by the next "
                                                 "solve instead of the solver's own choice (docs/DISPATCH-PLANNER-V2.md §8.1)")

    class Meta:
        ordering = ["plan_id", "id"]
        indexes = [models.Index(fields=["plan", "status"])]

    def __str__(self):
        return f"{self.pickup.city}->{self.dropoff.city} ({self.weight_kg}kg)"


PLAN_VEHICLE_SOURCES = [("own", "Own"), ("attached", "Attached"), ("leased", "Leased"),
                        ("hired", "Hired (terms known)"), ("spot_slot", "Spot capacity slot")]


class PlanVehicle(Timestamped):
    """A vehicle's offer into one planning run, snapshotted so a plan stays
    reproducible after the fleet moves on from the values it was solved with."""
    plan = models.ForeignKey(DispatchPlan, on_delete=models.CASCADE, related_name="plan_vehicles")
    vehicle = models.ForeignKey("fleet.Vehicle", on_delete=models.SET_NULL, null=True, blank=True, related_name="plan_vehicles")
    driver = models.ForeignKey("fleet.Driver", on_delete=models.SET_NULL, null=True, blank=True, related_name="plan_vehicles")
    source = models.CharField(max_length=12, choices=PLAN_VEHICLE_SOURCES, default="own")
    vendor = models.ForeignKey("fleet.Vendor", on_delete=models.SET_NULL, null=True, blank=True, related_name="plan_vehicles")
    start_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    start_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    start_place = models.ForeignKey("fleet.Place", on_delete=models.SET_NULL, null=True, blank=True, related_name="plan_vehicle_starts")
    position_stale = models.BooleanField(default=False)
    available_from = models.DateTimeField(default=timezone.now)
    must_return_to = models.ForeignKey("fleet.Place", on_delete=models.SET_NULL, null=True, blank=True, related_name="plan_vehicle_returns")
    capacity_kg = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    capacity_cbm = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    temperature_class = models.CharField(max_length=10, choices=TEMPERATURE_CLASSES + [("multi", "Multi")], default="dry")
    cost_per_km = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cost_per_hour = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fixed_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    max_stops = models.PositiveIntegerField(default=20)
    max_route_km = models.PositiveIntegerField(default=800)
    max_duty_minutes = models.PositiveIntegerField(default=600)
    locked_to_route = models.BooleanField(default=False)
    excluded = models.BooleanField(default=False)
    exclusion_reason = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["plan_id", "id"]

    def __str__(self):
        return getattr(self.vehicle, "registration_number", None) or f"hire-{self.pk}"


class PlannedRoute(Timestamped):
    """One vehicle's day within a plan."""
    plan = models.ForeignKey(DispatchPlan, on_delete=models.CASCADE, related_name="routes")
    plan_vehicle = models.ForeignKey(PlanVehicle, on_delete=models.CASCADE, related_name="routes")
    sequence = models.PositiveIntegerField(default=1)
    total_distance_km = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_duration_minutes = models.PositiveIntegerField(default=0)
    drive_minutes = models.PositiveIntegerField(default=0)
    wait_minutes = models.PositiveIntegerField(default=0)
    dead_km = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    max_load_kg = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    utilisation_weight_percent = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    utilisation_volume_percent = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True,
                                                      help_text="Null when the vehicle's volume capacity was never recorded, not 0")
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    estimated_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    estimated_margin = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    temperature_class = models.CharField(max_length=10, default="dry")
    feasible = models.BooleanField(default=True)
    violations = models.JSONField(default=list, blank=True)
    locked = models.BooleanField(default=False)
    committed_trip = models.ForeignKey("fleet.Trip", on_delete=models.SET_NULL, null=True, blank=True, related_name="dispatch_routes")

    class Meta:
        ordering = ["plan_id", "sequence"]

    def __str__(self):
        return f"{self.plan.code} route #{self.sequence}"


STOP_TYPES = [("pickup", "Pickup"), ("drop", "Drop"), ("rest", "Rest"), ("return", "Return")]


class PlannedStop(Timestamped):
    route = models.ForeignKey(PlannedRoute, on_delete=models.CASCADE, related_name="stops")
    sequence = models.PositiveIntegerField(default=1)
    task = models.ForeignKey(DispatchTask, on_delete=models.SET_NULL, null=True, blank=True, related_name="stops")
    place = models.ForeignKey("fleet.Place", on_delete=models.PROTECT, related_name="planned_stops")
    stop_type = models.CharField(max_length=10, choices=STOP_TYPES, default="drop")
    planned_arrival = models.DateTimeField(null=True, blank=True)
    planned_departure = models.DateTimeField(null=True, blank=True)
    service_minutes = models.PositiveIntegerField(default=0)
    wait_minutes = models.PositiveIntegerField(default=0)
    load_after_kg = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    load_after_cbm = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    distance_from_previous_km = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    locked = models.BooleanField(default=False)
    actual_arrival = models.DateTimeField(null=True, blank=True)
    actual_departure = models.DateTimeField(null=True, blank=True)
    variance_minutes = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["route_id", "sequence"]
        unique_together = [("route", "sequence")]

    def __str__(self):
        return f"{self.route_id} #{self.sequence} {self.stop_type}"


PLAN_EVENT_TYPES = [("created", "Created"), ("collected", "Demand collected"), ("solved", "Solved"),
                    ("task_dropped", "Task dropped"), ("manual_move", "Manual move"),
                    ("hire_requested", "Hire requested"), ("quote_accepted", "Quote accepted"),
                    ("committed", "Committed"), ("route_committed", "Route committed"), ("replanned", "Re-planned"),
                    ("scenario_compared", "Scenario compared"), ("scenario_adopted", "Scenario adopted")]


class PlanEvent(Timestamped):
    """Append-only audit trail: why did this load go where it went."""
    plan = models.ForeignKey(DispatchPlan, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=20, choices=PLAN_EVENT_TYPES)
    description = models.CharField(max_length=240, blank=True)
    data = models.JSONField(default=dict, blank=True)
    actor = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.plan_id} {self.event_type}"


TRAVEL_PROVIDERS = [("haversine", "Haversine estimate"), ("osrm", "OSRM"), ("google", "Google Distance Matrix"),
                    ("learned", "Learned from GPS traces")]


class TravelMatrixEntry(Timestamped):
    """Cached distance/duration between two points, so a warm plan does not repay
    the cost of a distance-matrix call (or even a haversine loop) every solve."""
    origin_key = models.CharField(max_length=60)
    destination_key = models.CharField(max_length=60)
    distance_km = models.DecimalField(max_digits=10, decimal_places=2)
    duration_minutes = models.DecimalField(max_digits=8, decimal_places=1)
    provider = models.CharField(max_length=10, choices=TRAVEL_PROVIDERS, default="haversine")
    vehicle_class = models.CharField(max_length=20, blank=True)
    time_bucket = models.CharField(max_length=10, blank=True, help_text="Hour-of-day band, e.g. '08-10'")
    fetched_at = models.DateTimeField(default=timezone.now)
    hit_count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("origin_key", "destination_key", "provider", "vehicle_class", "time_bucket")]
        indexes = [models.Index(fields=["origin_key", "destination_key"])]

    def __str__(self):
        return f"{self.origin_key}->{self.destination_key} ({self.distance_km}km)"


HIRE_REQUIREMENT_STATUSES = [("open", "Open"), ("quoted", "Quoted"), ("awarded", "Awarded"), ("cancelled", "Cancelled")]


class HireRequirement(Timestamped):
    """Capacity a plan needs but the own fleet cannot supply - what the solver
    chose to outsource, grouped for an RFQ rather than negotiated load by load."""
    plan = models.ForeignKey(DispatchPlan, on_delete=models.CASCADE, related_name="hire_requirements")
    tasks = models.ManyToManyField(DispatchTask, related_name="hire_requirements", blank=True)
    vehicle_type = models.CharField(max_length=60, blank=True)
    temperature_class = models.CharField(max_length=10, choices=TEMPERATURE_CLASSES, default="dry")
    capacity_kg = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pickup = models.ForeignKey("fleet.Place", on_delete=models.SET_NULL, null=True, blank=True, related_name="hire_requirement_pickups")
    dropoff = models.ForeignKey("fleet.Place", on_delete=models.SET_NULL, null=True, blank=True, related_name="hire_requirement_dropoffs")
    report_by = models.DateTimeField(null=True, blank=True)
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=HIRE_REQUIREMENT_STATUSES, default="open")
    awarded_hire = models.ForeignKey("fleet.VehicleHire", on_delete=models.SET_NULL, null=True, blank=True, related_name="hire_requirement")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.plan.code}: {self.vehicle_type or 'any'} {self.capacity_kg}kg"


CARRIER_OFFER_STATUSES = [("invited", "Invited"), ("quoted", "Quoted"), ("accepted", "Accepted"),
                          ("rejected", "Rejected"), ("expired", "Expired")]


class CarrierOffer(Timestamped):
    """One vendor's response to a `HireRequirement` RFQ - what §8.2/§8.3 of
    docs/DISPATCH-PLANNING.md calls sourcing and award. Accepting one creates the
    existing `fleet.VehicleHire`, so the commercial record stays singular."""
    requirement = models.ForeignKey(HireRequirement, on_delete=models.CASCADE, related_name="offers")
    vendor = models.ForeignKey("fleet.Vendor", on_delete=models.CASCADE, related_name="carrier_offers")
    offered_rate = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    rate_basis = models.CharField(max_length=10, default="trip")
    vehicle_number = models.CharField(max_length=20, blank=True)
    vehicle_type = models.CharField(max_length=60, blank=True)
    temperature_class = models.CharField(max_length=10, choices=TEMPERATURE_CLASSES, default="dry")
    gps_available = models.BooleanField(default=False)
    driver_name = models.CharField(max_length=120, blank=True)
    driver_phone = models.CharField(max_length=20, blank=True)
    message_id = models.PositiveIntegerField(null=True, blank=True, help_text="iam.OutboundMessage this RFQ was sent as")
    valid_until = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=CARRIER_OFFER_STATUSES, default="invited")
    responded_at = models.DateTimeField(null=True, blank=True)
    remarks = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["offered_rate", "-created_at"]
        indexes = [models.Index(fields=["requirement", "status"])]

    def __str__(self):
        return f"{self.vendor.name} -> {self.requirement_id} ({self.status})"
