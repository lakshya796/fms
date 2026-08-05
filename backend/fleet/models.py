from datetime import timedelta
from secrets import randbelow
from uuid import uuid4
from decimal import Decimal, ROUND_HALF_UP
from math import asin, cos, radians, sin, sqrt

from django.db import models
from django.utils import timezone

def money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km, used for geofence checks and lane distances."""
    lat1, lon1, lat2, lon2 = (radians(float(v)) for v in (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return round(2 * 6371.0088 * asin(sqrt(a)), 3)

class Timestamped(models.Model):
    created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
    class Meta: abstract=True

class Customer(Timestamped):
    name=models.CharField(max_length=180); gstin=models.CharField(max_length=15,unique=True); pan=models.CharField(max_length=10,blank=True)
    phone=models.CharField(max_length=20,blank=True); email=models.EmailField(blank=True); billing_address=models.TextField(blank=True)
    credit_limit=models.DecimalField(max_digits=14,decimal_places=2,default=0); kyc_status=models.CharField(max_length=20,default="pending")
    def __str__(self): return self.name

class Driver(Timestamped):
    name=models.CharField(max_length=120); phone=models.CharField(max_length=20,unique=True); licence_number=models.CharField(max_length=40,unique=True)
    licence_expiry=models.DateField(null=True,blank=True); status=models.CharField(max_length=20,default="available")
    current_latitude=models.DecimalField(max_digits=9,decimal_places=6,null=True,blank=True); current_longitude=models.DecimalField(max_digits=9,decimal_places=6,null=True,blank=True)
    aadhaar_number=models.CharField(max_length=12,blank=True); date_of_joining=models.DateField(null=True,blank=True); home_city=models.CharField(max_length=80,blank=True)
    monthly_salary=models.DecimalField(max_digits=10,decimal_places=2,default=0); daily_allowance=models.DecimalField(max_digits=8,decimal_places=2,default=0)
    def __str__(self): return self.name

class Vehicle(Timestamped):
    registration_number=models.CharField(max_length=20,unique=True); vehicle_type=models.CharField(max_length=60); capacity_kg=models.PositiveIntegerField(default=0)
    ownership=models.CharField(max_length=20,default="owned"); status=models.CharField(max_length=20,default="available"); gps_device_id=models.CharField(max_length=100,blank=True)
    insurance_expiry=models.DateField(null=True,blank=True); permit_expiry=models.DateField(null=True,blank=True)
    make_model=models.CharField(max_length=120,blank=True); chassis_number=models.CharField(max_length=40,blank=True); engine_number=models.CharField(max_length=40,blank=True)
    fuel_type=models.CharField(max_length=20,default="diesel"); fastag_id=models.CharField(max_length=40,blank=True); current_odometer_km=models.PositiveIntegerField(default=0)
    def __str__(self): return self.registration_number

class LorryReceipt(Timestamped):
    number=models.CharField(max_length=30,unique=True); customer=models.ForeignKey(Customer,on_delete=models.PROTECT,related_name="lorry_receipts")
    consignor=models.CharField(max_length=180); consignee=models.CharField(max_length=180); origin=models.CharField(max_length=120); destination=models.CharField(max_length=120)
    material=models.CharField(max_length=180); weight_kg=models.DecimalField(max_digits=12,decimal_places=2); packages=models.PositiveIntegerField(default=1)
    eway_bill_number=models.CharField(max_length=30,blank=True); freight_amount=models.DecimalField(max_digits=12,decimal_places=2,default=0); status=models.CharField(max_length=20,default="booked")
    def __str__(self): return self.number

class Trip(Timestamped):
    number=models.CharField(max_length=30,unique=True); vehicle=models.ForeignKey(Vehicle,on_delete=models.PROTECT,related_name="trips")
    driver=models.ForeignKey(Driver,on_delete=models.PROTECT,related_name="trips"); lorry_receipts=models.ManyToManyField(LorryReceipt,related_name="trips")
    origin=models.CharField(max_length=120); destination=models.CharField(max_length=120); planned_departure=models.DateTimeField()
    actual_departure=models.DateTimeField(null=True,blank=True); arrival_at=models.DateTimeField(null=True,blank=True)
    advance_amount=models.DecimalField(max_digits=12,decimal_places=2,default=0); estimated_cost=models.DecimalField(max_digits=12,decimal_places=2,default=0); status=models.CharField(max_length=20,default="planned")
    def __str__(self): return self.number

class TrackingEvent(Timestamped):
    trip=models.ForeignKey(Trip,on_delete=models.CASCADE,related_name="tracking_events"); event_type=models.CharField(max_length=40,default="position")
    latitude=models.DecimalField(max_digits=9,decimal_places=6); longitude=models.DecimalField(max_digits=9,decimal_places=6); speed_kph=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    recorded_at=models.DateTimeField(); description=models.CharField(max_length=240,blank=True)

class Invoice(Timestamped):
    number=models.CharField(max_length=30,unique=True); customer=models.ForeignKey(Customer,on_delete=models.PROTECT,related_name="invoices")
    trip=models.ForeignKey(Trip,on_delete=models.PROTECT,related_name="invoices",null=True,blank=True)
    freight_amount=models.DecimalField(max_digits=12,decimal_places=2,default=0)
    additional_charges=models.DecimalField(max_digits=12,decimal_places=2,default=0); tax_amount=models.DecimalField(max_digits=12,decimal_places=2,default=0)
    total_amount=models.DecimalField(max_digits=12,decimal_places=2,default=0); due_date=models.DateField(); status=models.CharField(max_length=20,default="draft")
    order=models.ForeignKey("Order",on_delete=models.SET_NULL,null=True,blank=True,related_name="invoices",
                           help_text="The consignment this bills. Freight and GST are taken from it.")
    gst_percent=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    reverse_charge=models.BooleanField(default=False,help_text="GST payable by the consignee under RCM")
    place_of_supply=models.CharField(max_length=80,blank=True)

    def save(self,*args,**kwargs):
        # The total is always the sum of its parts; typing it by hand invites mistakes.
        self.total_amount = money(self.freight_amount) + money(self.additional_charges) + money(self.tax_amount)
        super().save(*args,**kwargs)

    def __str__(self): return self.number

class Settlement(Timestamped):
    trip=models.ForeignKey(Trip,on_delete=models.PROTECT,related_name="settlements"); driver=models.ForeignKey(Driver,on_delete=models.PROTECT,related_name="settlements")
    advance_amount=models.DecimalField(max_digits=12,decimal_places=2,default=0); approved_expenses=models.DecimalField(max_digits=12,decimal_places=2,default=0)
    net_payable=models.DecimalField(max_digits=12,decimal_places=2,default=0); status=models.CharField(max_length=20,default="pending")

class SalesQuote(Timestamped):
    number=models.CharField(max_length=30,unique=True); customer=models.ForeignKey(Customer,on_delete=models.PROTECT,related_name="quotes")
    origin=models.CharField(max_length=120); destination=models.CharField(max_length=120); freight_amount=models.DecimalField(max_digits=12,decimal_places=2)
    valid_until=models.DateField(); status=models.CharField(max_length=20,default="draft")
    def __str__(self): return self.number

class MaintenanceWorkOrder(Timestamped):
    number=models.CharField(max_length=30,unique=True); vehicle=models.ForeignKey(Vehicle,on_delete=models.PROTECT,related_name="work_orders")
    title=models.CharField(max_length=180); category=models.CharField(max_length=40,default="preventive"); scheduled_date=models.DateField()
    odometer_km=models.PositiveIntegerField(default=0); estimated_cost=models.DecimalField(max_digits=12,decimal_places=2,default=0)
    vendor=models.CharField(max_length=180,blank=True); status=models.CharField(max_length=20,default="open")
    def __str__(self): return self.number


# ---------------------------------------------------------------------------
# Fleetbase FleetOps inspired layer, adapted for Indian fleet owners.
# Master data: Vendor, Place, ServiceArea, Zone, Fleet
# Rating: ServiceRate, ServiceQuote
# Operations: Order, Waypoint, TrackingActivity, ProofOfDelivery
# Fleet care: FuelEntry, TripExpense, Issue, ComplianceDocument, MaintenanceSchedule
# ---------------------------------------------------------------------------

VENDOR_TYPES = [("transporter", "Attached transporter"), ("broker", "Broker / commission agent"), ("workshop", "Workshop"),
                ("fuel", "Fuel station"), ("tyre", "Tyre vendor"), ("insurance", "Insurance"), ("other", "Other")]
PLACE_TYPES = [("warehouse", "Warehouse"), ("hub", "Branch / hub"), ("customer", "Customer site"), ("plant", "Plant"),
               ("fuel_station", "Fuel station"), ("toll_plaza", "Toll plaza"), ("workshop", "Workshop"),
               ("parking", "Truck parking"), ("checkpost", "RTO check post")]
ZONE_TYPES = [("delivery", "Delivery zone"), ("pickup", "Pickup zone"), ("hub", "Hub zone"), ("restricted", "Restricted zone")]
RATE_TYPES = [("per_km", "Per km"), ("per_ton_km", "Per ton per km"), ("per_kg", "Per kg"), ("per_trip", "Fixed per trip"), ("per_hour", "Per hour")]
ORDER_TYPES = [("ftl", "Full truck load"), ("ptl", "Part truck load"), ("parcel", "Parcel"), ("rental", "Vehicle rental"), ("reverse", "Reverse pickup")]
ORDER_STATUSES = ["created", "assigned", "dispatched", "in_transit", "at_dropoff", "completed", "cancelled"]
PAYMENT_MODES = [("to_pay", "To pay"), ("paid", "Paid"), ("tbb", "To be billed"), ("cod", "Cash on delivery")]
FUEL_PAYMENT_MODES = [("fastag", "FASTag"), ("fuel_card", "Fuel card"), ("cash", "Cash"), ("upi", "UPI"), ("credit", "Station credit")]
EXPENSE_CATEGORIES = [("diesel", "Diesel"), ("toll", "Toll / FASTag"), ("driver_allowance", "Driver bhatta"), ("loading", "Loading"),
                      ("unloading", "Unloading"), ("rto_fine", "RTO fine"), ("police", "Police / checkpost"), ("parking", "Parking"),
                      ("repair", "On-road repair"), ("permit", "Permit / border tax"), ("halting", "Halting charges"), ("other", "Other")]
ISSUE_TYPES = [("breakdown", "Breakdown"), ("accident", "Accident"), ("tyre", "Tyre"), ("documents", "Documents"), ("delay", "Delay"),
               ("route", "Route deviation"), ("safety", "Safety"), ("fuel_theft", "Fuel pilferage"), ("other", "Other")]
DOCUMENT_TYPES = [("rc", "Registration certificate"), ("insurance", "Insurance"), ("fitness", "Fitness certificate"),
                  ("permit_national", "National permit"), ("permit_state", "State permit"), ("puc", "PUC certificate"),
                  ("road_tax", "Road tax"), ("fastag", "FASTag KYC"), ("gps_certificate", "GPS/VLT certificate"),
                  ("licence", "Driving licence"), ("aadhaar", "Aadhaar"), ("police_verification", "Police verification"), ("medical", "Medical fitness")]


class Vendor(Timestamped):
    """Attached transporters, brokers and service vendors (Fleetbase Vendor)."""
    name = models.CharField(max_length=180)
    code = models.CharField(max_length=30, unique=True)
    vendor_type = models.CharField(max_length=20, choices=VENDOR_TYPES, default="transporter")
    contact_person = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    gstin = models.CharField(max_length=15, blank=True)
    pan = models.CharField(max_length=10, blank=True)
    msme_number = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=80, blank=True)
    bank_account = models.CharField(max_length=30, blank=True)
    ifsc = models.CharField(max_length=15, blank=True)
    payment_terms_days = models.PositiveIntegerField(default=30)
    tds_percent = models.DecimalField(max_digits=5, decimal_places=2, default=2)
    status = models.CharField(max_length=20, default="active")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ServiceArea(Timestamped):
    """A named operating region such as 'West India' (Fleetbase ServiceArea)."""
    name = models.CharField(max_length=120, unique=True)
    code = models.CharField(max_length=20, unique=True)
    states = models.CharField(max_length=240, blank=True, help_text="Comma separated Indian states covered")
    description = models.CharField(max_length=240, blank=True)
    boundary = models.JSONField(default=list, blank=True, help_text="Optional [[lat, lng], ...] outline")
    status = models.CharField(max_length=20, default="active")

    class Meta:
        ordering = ["name"]

    @property
    def state_list(self):
        return [s.strip() for s in self.states.split(",") if s.strip()]

    def __str__(self):
        return self.name


class Zone(Timestamped):
    """Circular geofence inside a service area (Fleetbase Zone)."""
    service_area = models.ForeignKey(ServiceArea, on_delete=models.CASCADE, related_name="zones")
    name = models.CharField(max_length=120)
    zone_type = models.CharField(max_length=20, choices=ZONE_TYPES, default="delivery")
    center_latitude = models.DecimalField(max_digits=9, decimal_places=6)
    center_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    radius_km = models.DecimalField(max_digits=7, decimal_places=2, default=10)
    colour = models.CharField(max_length=9, default="#0d5f45")
    status = models.CharField(max_length=20, default="active")

    class Meta:
        ordering = ["service_area__name", "name"]
        unique_together = [("service_area", "name")]

    def distance_km(self, latitude, longitude):
        return haversine_km(self.center_latitude, self.center_longitude, latitude, longitude)

    def contains(self, latitude, longitude):
        return self.distance_km(latitude, longitude) <= float(self.radius_km)

    def __str__(self):
        return f"{self.name} ({self.service_area.code})"


class Place(Timestamped):
    """Saved pickup/drop location with Indian address fields (Fleetbase Place)."""
    name = models.CharField(max_length=180)
    code = models.CharField(max_length=30, unique=True)
    place_type = models.CharField(max_length=20, choices=PLACE_TYPES, default="customer")
    service_area = models.ForeignKey(ServiceArea, on_delete=models.SET_NULL, null=True, blank=True, related_name="places")
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="places")
    address = models.TextField(blank=True)
    landmark = models.CharField(max_length=180, blank=True)
    city = models.CharField(max_length=80)
    state = models.CharField(max_length=80, blank=True)
    pincode = models.CharField(max_length=6, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    contact_name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    loading_hours = models.CharField(max_length=60, blank=True, help_text="e.g. 09:00-18:00")
    status = models.CharField(max_length=20, default="active")

    class Meta:
        ordering = ["name"]

    def zone(self):
        if self.latitude is None or self.longitude is None:
            return None
        zones = Zone.objects.filter(status="active")
        if self.service_area_id:
            zones = zones.filter(service_area_id=self.service_area_id)
        return next((z for z in zones if z.contains(self.latitude, self.longitude)), None)

    def __str__(self):
        return f"{self.name}, {self.city}"


class Fleet(Timestamped):
    """A named group of vehicles and drivers (Fleetbase Fleet/FleetVehicle/FleetDriver)."""
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, unique=True)
    service_area = models.ForeignKey(ServiceArea, on_delete=models.SET_NULL, null=True, blank=True, related_name="fleets")
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name="fleets")
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="sub_fleets")
    manager = models.CharField(max_length=120, blank=True)
    vehicles = models.ManyToManyField(Vehicle, blank=True, related_name="fleets")
    drivers = models.ManyToManyField(Driver, blank=True, related_name="fleets")
    status = models.CharField(max_length=20, default="active")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ServiceRate(Timestamped):
    """Freight rate card with Indian GST handling (Fleetbase ServiceRate)."""
    name = models.CharField(max_length=140)
    code = models.CharField(max_length=30, unique=True)
    service_area = models.ForeignKey(ServiceArea, on_delete=models.SET_NULL, null=True, blank=True, related_name="service_rates")
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="service_rates")
    vehicle_type = models.CharField(max_length=60, blank=True)
    rate_type = models.CharField(max_length=20, choices=RATE_TYPES, default="per_km")
    base_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    per_km_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    per_kg_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    per_ton_km_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    per_hour_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    minimum_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    loading_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    unloading_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    halting_charge_per_day = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fuel_surcharge_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    gst_percent = models.DecimalField(max_digits=5, decimal_places=2, default=5, help_text="5% GTA without ITC, 12% with ITC")
    reverse_charge = models.BooleanField(default=False, help_text="GST payable by consignee under RCM")
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, default="active")

    class Meta:
        ordering = ["name"]

    def quote(self, distance_km=0, weight_kg=0, hours=0, halt_days=0, other_charges=0):
        """Return a full freight breakdown for the given lane inputs."""
        distance = Decimal(str(distance_km or 0))
        weight = Decimal(str(weight_kg or 0))
        if self.rate_type == "per_km":
            freight = self.per_km_rate * distance
        elif self.rate_type == "per_ton_km":
            freight = self.per_ton_km_rate * (weight / Decimal("1000")) * distance
        elif self.rate_type == "per_kg":
            freight = self.per_kg_rate * weight
        elif self.rate_type == "per_hour":
            freight = self.per_hour_rate * Decimal(str(hours or 0))
        else:
            freight = Decimal("0")
        freight = money(freight + self.base_charge)
        if self.minimum_charge and freight < self.minimum_charge:
            freight = money(self.minimum_charge)
        surcharge = money(freight * self.fuel_surcharge_percent / Decimal("100"))
        handling = money(self.loading_charge + self.unloading_charge + self.halting_charge_per_day * Decimal(str(halt_days or 0)))
        extras = money(other_charges)
        taxable = money(freight + surcharge + handling + extras)
        tax = Decimal("0") if self.reverse_charge else money(taxable * self.gst_percent / Decimal("100"))
        return {
            "rate_card": self.name, "rate_type": self.rate_type, "distance_km": float(distance), "weight_kg": float(weight),
            "freight": float(freight), "fuel_surcharge": float(surcharge), "handling_charges": float(handling),
            "other_charges": float(extras), "taxable_value": float(taxable), "gst_percent": float(self.gst_percent),
            "gst_amount": float(tax), "reverse_charge": self.reverse_charge, "total": float(money(taxable + tax)),
        }

    def __str__(self):
        return self.name


class ServiceQuote(Timestamped):
    """A priced lane estimate produced from a rate card (Fleetbase ServiceQuote)."""
    number = models.CharField(max_length=30, unique=True)
    service_rate = models.ForeignKey(ServiceRate, on_delete=models.PROTECT, related_name="quotes")
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="service_quotes")
    origin = models.CharField(max_length=120)
    destination = models.CharField(max_length=120)
    distance_km = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    weight_kg = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    breakdown = models.JSONField(default=dict, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valid_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, default="draft")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number


class Order(Timestamped):
    """Customer consignment order with waypoints and tracking (Fleetbase Order)."""
    number = models.CharField(max_length=30, unique=True)
    tracking_number = models.CharField(max_length=24, unique=True, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    branch = models.ForeignKey("iam.Branch", on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    order_type = models.CharField(max_length=20, choices=ORDER_TYPES, default="ftl")
    service_rate = models.ForeignKey(ServiceRate, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    fleet = models.ForeignKey(Fleet, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    pickup = models.ForeignKey(Place, on_delete=models.PROTECT, related_name="pickup_orders")
    dropoff = models.ForeignKey(Place, on_delete=models.PROTECT, related_name="dropoff_orders")
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    lorry_receipt = models.ForeignKey(LorryReceipt, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    trip = models.ForeignKey(Trip, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    scheduled_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    payload_description = models.CharField(max_length=240, blank=True)
    weight_kg = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    volume_cbm = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    packages = models.PositiveIntegerField(default=1)
    declared_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    distance_km = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    freight_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_charges = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cod_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_mode = models.CharField(max_length=20, choices=PAYMENT_MODES, default="to_pay")
    eway_bill_number = models.CharField(max_length=30, blank=True)
    priority = models.CharField(max_length=20, default="normal")
    pod_required = models.BooleanField(default=True)
    special_instructions = models.TextField(blank=True)
    status = models.CharField(max_length=20, default="created")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"]), models.Index(fields=["tracking_number"]),
                   models.Index(fields=["branch", "status"])]

    def save(self, *args, **kwargs):
        if not self.tracking_number:
            self.tracking_number = "PHZ" + timezone.now().strftime("%y%m%d") + uuid4().hex[:8].upper()
        super().save(*args, **kwargs)

    def price_from_rate_card(self, save=True):
        """Recalculate freight, GST and total from the linked rate card."""
        if not self.service_rate:
            return None
        breakdown = self.service_rate.quote(distance_km=self.distance_km, weight_kg=self.weight_kg, other_charges=self.other_charges)
        self.freight_amount = money(breakdown["freight"] + breakdown["fuel_surcharge"] + breakdown["handling_charges"])
        self.tax_amount = money(breakdown["gst_amount"])
        self.total_amount = money(breakdown["total"])
        if save:
            self.save(update_fields=["freight_amount", "tax_amount", "total_amount", "updated_at"])
        return breakdown

    def log(self, status, code, details="", latitude=None, longitude=None, city=""):
        return TrackingActivity.objects.create(order=self, status=status, code=code, details=details,
                                               latitude=latitude, longitude=longitude, city=city, recorded_at=timezone.now())

    def __str__(self):
        return self.number


class Waypoint(Timestamped):
    """An ordered stop on a multi-drop order (Fleetbase Waypoint)."""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="waypoints")
    place = models.ForeignKey(Place, on_delete=models.PROTECT, related_name="waypoints")
    sequence = models.PositiveIntegerField(default=1)
    waypoint_type = models.CharField(max_length=20, default="drop")
    planned_arrival = models.DateTimeField(null=True, blank=True)
    actual_arrival = models.DateTimeField(null=True, blank=True)
    contact_name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    notes = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=20, default="pending")

    class Meta:
        ordering = ["order_id", "sequence"]
        unique_together = [("order", "sequence")]

    def __str__(self):
        return f"{self.order.number} #{self.sequence}"


class TrackingActivity(Timestamped):
    """Customer visible status feed for an order (Fleetbase TrackingStatus)."""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="activities")
    status = models.CharField(max_length=40)
    code = models.CharField(max_length=40)
    details = models.CharField(max_length=240, blank=True)
    city = models.CharField(max_length=80, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-recorded_at", "-id"]

    def __str__(self):
        return f"{self.order_id} {self.code}"


POD_STATUSES = [("awaiting", "Awaiting delivery"), ("submitted", "Submitted, awaiting review"),
                ("verified", "Verified"), ("rejected", "Rejected")]


class ProofOfDelivery(Timestamped):
    """ePOD for a consignment: an OTP issued to the consignee, what the driver captured
    at the drop, and the office review that follows.

    An order that requires proof cannot be completed, and cannot be invoiced, until a
    proof here reaches `verified`.
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="proofs")
    waypoint = models.ForeignKey(Waypoint, on_delete=models.SET_NULL, null=True, blank=True, related_name="proofs")
    proof_type = models.CharField(max_length=20, default="signature")
    receiver_name = models.CharField(max_length=120, blank=True)
    receiver_phone = models.CharField(max_length=20, blank=True)
    remarks = models.CharField(max_length=240, blank=True)
    file_url = models.URLField(blank=True, help_text="Signature image, photo of the signed LR, or scanned POD")
    otp = models.CharField(max_length=6, blank=True, help_text="Issued to the consignee, quoted back by the driver")
    otp_issued_at = models.DateTimeField(null=True, blank=True)
    otp_verified = models.BooleanField(default=False)
    shortage_kg = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    damage_reported = models.BooleanField(default=False)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    captured_at = models.DateTimeField(null=True, blank=True, help_text="When the driver submitted it")
    status = models.CharField(max_length=20, choices=POD_STATUSES, default="awaiting")
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.CharField(max_length=150, blank=True)
    rejection_reason = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]
        verbose_name_plural = "proofs of delivery"

    OTP_VALID_FOR = timedelta(hours=24)

    def issue_otp(self):
        """Generate the code the consignee quotes to the driver at the drop."""
        self.otp = f"{randbelow(1000000):06d}"
        self.otp_issued_at = timezone.now()
        self.otp_verified = False
        self.status = "awaiting"
        self.save(update_fields=["otp", "otp_issued_at", "otp_verified", "status", "updated_at"])
        return self.otp

    @property
    def otp_expired(self):
        return bool(self.otp_issued_at) and timezone.now() - self.otp_issued_at > self.OTP_VALID_FOR

    @property
    def is_clean(self):
        """No shortage and no damage, so it can clear review without a second look."""
        return not self.damage_reported and not self.shortage_kg

    def settle(self):
        """Decide where a fresh capture lands.

        An OTP the consignee quoted back, or a signed POD on file, with nothing short
        and nothing damaged, clears on its own. Everything else waits for the office,
        because a shortage becomes a deduction on the bill.
        """
        confirmed = self.otp_verified or bool(self.file_url)
        if confirmed and self.is_clean:
            self.status = "verified"
            self.verified_at = timezone.now()
            self.verified_by = self.verified_by or "Confirmed at delivery"
        else:
            self.status = "submitted"
        return self.status

    def __str__(self):
        return f"POD {self.order_id} ({self.status})"


class FuelEntry(Timestamped):
    """Diesel fill-up with automatic mileage tracking (Fleetbase FuelReport)."""
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="fuel_entries")
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True, related_name="fuel_entries")
    trip = models.ForeignKey(Trip, on_delete=models.SET_NULL, null=True, blank=True, related_name="fuel_entries")
    station = models.ForeignKey(Place, on_delete=models.SET_NULL, null=True, blank=True, related_name="fuel_entries")
    station_name = models.CharField(max_length=180, blank=True)
    entry_date = models.DateField(default=timezone.localdate)
    odometer_km = models.PositiveIntegerField(default=0)
    volume_litres = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    rate_per_litre = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=20, choices=FUEL_PAYMENT_MODES, default="fuel_card")
    invoice_number = models.CharField(max_length=40, blank=True)
    mileage_kmpl = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    remarks = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["-entry_date", "-id"]
        verbose_name_plural = "fuel entries"

    def save(self, *args, **kwargs):
        if not self.amount:
            self.amount = money(self.volume_litres * self.rate_per_litre)
        previous = FuelEntry.objects.filter(vehicle=self.vehicle, odometer_km__lt=self.odometer_km).exclude(pk=self.pk).order_by("-odometer_km").first()
        if previous and self.volume_litres:
            self.mileage_kmpl = money(Decimal(self.odometer_km - previous.odometer_km) / self.volume_litres)
        super().save(*args, **kwargs)
        if self.odometer_km > self.vehicle.current_odometer_km:
            Vehicle.objects.filter(pk=self.vehicle_id).update(current_odometer_km=self.odometer_km)

    def __str__(self):
        return f"{self.vehicle_id} {self.entry_date} {self.volume_litres}L"


class TripExpense(Timestamped):
    """On-road trip cost such as toll, bhatta, loading or an RTO fine."""
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, null=True, blank=True, related_name="expenses")
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses")
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses")
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses")
    category = models.CharField(max_length=24, choices=EXPENSE_CATEGORIES, default="toll")
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    expense_date = models.DateField(default=timezone.localdate)
    paid_by = models.CharField(max_length=20, default="driver")
    receipt_number = models.CharField(max_length=40, blank=True)
    remarks = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=20, default="pending")

    class Meta:
        ordering = ["-expense_date", "-id"]

    def __str__(self):
        return f"{self.category} {self.amount}"


class Issue(Timestamped):
    """Driver or dispatcher reported incident (Fleetbase Issue)."""
    number = models.CharField(max_length=30, unique=True)
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True, related_name="issues")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True, related_name="issues")
    trip = models.ForeignKey(Trip, on_delete=models.SET_NULL, null=True, blank=True, related_name="issues")
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="issues")
    issue_type = models.CharField(max_length=20, choices=ISSUE_TYPES, default="breakdown")
    priority = models.CharField(max_length=20, default="medium")
    description = models.TextField(blank=True)
    location_text = models.CharField(max_length=180, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    assigned_to = models.CharField(max_length=120, blank=True)
    resolution = models.CharField(max_length=240, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default="open")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number


class ComplianceDocument(Timestamped):
    """Statutory vehicle and driver papers with expiry reminders."""
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, null=True, blank=True, related_name="documents")
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, null=True, blank=True, related_name="documents")
    document_type = models.CharField(max_length=24, choices=DOCUMENT_TYPES, default="insurance")
    number = models.CharField(max_length=60, blank=True)
    issuing_authority = models.CharField(max_length=140, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    reminder_days = models.PositiveIntegerField(default=30)
    file_url = models.URLField(blank=True)
    notes = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["expiry_date"]

    @property
    def days_to_expiry(self):
        if not self.expiry_date:
            return None
        return (self.expiry_date - timezone.localdate()).days

    @property
    def status(self):
        days = self.days_to_expiry
        if days is None:
            return "unknown"
        if days < 0:
            return "expired"
        return "expiring" if days <= self.reminder_days else "valid"

    def __str__(self):
        return f"{self.document_type} {self.number}"


class MaintenanceSchedule(Timestamped):
    """Preventive service plan driven by odometer or calendar interval."""
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="maintenance_schedules")
    task = models.CharField(max_length=180)
    interval_km = models.PositiveIntegerField(default=0)
    interval_days = models.PositiveIntegerField(default=0)
    last_service_km = models.PositiveIntegerField(default=0)
    last_service_date = models.DateField(null=True, blank=True)
    next_due_km = models.PositiveIntegerField(default=0)
    next_due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, default="active")

    class Meta:
        ordering = ["vehicle__registration_number", "task"]

    def save(self, *args, **kwargs):
        if self.interval_km:
            self.next_due_km = self.last_service_km + self.interval_km
        if self.interval_days and self.last_service_date:
            self.next_due_date = self.last_service_date + timedelta(days=self.interval_days)
        super().save(*args, **kwargs)

    @property
    def km_remaining(self):
        if not self.next_due_km:
            return None
        return self.next_due_km - self.vehicle.current_odometer_km

    @property
    def is_due(self):
        km_left = self.km_remaining
        if km_left is not None and km_left <= 0:
            return True
        return bool(self.next_due_date and self.next_due_date <= timezone.localdate())

    def __str__(self):
        return f"{self.task} ({self.vehicle_id})"


# ---------------------------------------------------------------------------
# Operations flow for a large fleet: a customer indent is raised, allocated to a
# vehicle, and only then becomes a consignment order. Branch scoping lets each
# depot work its own book.
# ---------------------------------------------------------------------------

INDENT_STATUSES = [("open", "Open"), ("allocated", "Allocated"), ("converted", "Converted to order"),
                   ("cancelled", "Cancelled"), ("rejected", "Rejected")]


class Indent(Timestamped):
    """A customer's vehicle requirement, before a truck is committed to it.

    Large fleets take demand in advance and allocate against it each morning;
    the indent is what the allocation desk works from.
    """
    number = models.CharField(max_length=30, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="indents")
    branch = models.ForeignKey("iam.Branch", on_delete=models.SET_NULL, null=True, blank=True, related_name="indents")
    pickup = models.ForeignKey(Place, on_delete=models.PROTECT, related_name="pickup_indents")
    dropoff = models.ForeignKey(Place, on_delete=models.PROTECT, related_name="dropoff_indents")
    vehicle_type = models.CharField(max_length=60, blank=True)
    vehicles_required = models.PositiveIntegerField(default=1)
    material = models.CharField(max_length=180, blank=True)
    weight_kg = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    required_at = models.DateTimeField(null=True, blank=True)
    expected_rate = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    service_rate = models.ForeignKey(ServiceRate, on_delete=models.SET_NULL, null=True, blank=True, related_name="indents")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True, related_name="indents")
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True, related_name="indents")
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="indents")
    remarks = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=20, choices=INDENT_STATUSES, default="open")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return self.number
