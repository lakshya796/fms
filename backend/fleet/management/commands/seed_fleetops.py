"""Seed the FleetOps modules with a realistic Indian transport dataset.

Usage: python manage.py seed_fleetops [--reset]

The command is idempotent - re-running it updates the same demo records instead
of duplicating them, so it is safe to run against a staging database.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from fleet.models import (Customer, Driver, Vehicle, Vendor, ServiceArea, Zone, Place, Fleet, ServiceRate, Order,
                          Waypoint, FuelEntry, TripExpense, Issue, ComplianceDocument, MaintenanceSchedule)

SERVICE_AREAS = [
    ("West India", "WEST", "Maharashtra, Gujarat, Goa", 19.076, 72.8777),
    ("North India", "NORTH", "Delhi, Haryana, Punjab, Rajasthan, Uttar Pradesh", 28.6139, 77.209),
    ("South India", "SOUTH", "Karnataka, Tamil Nadu, Telangana, Kerala", 12.9716, 77.5946),
]

ZONES = [
    ("WEST", "Mumbai metropolitan", "pickup", 19.076, 72.8777, 45),
    ("WEST", "Pune industrial belt", "delivery", 18.5204, 73.8567, 40),
    ("WEST", "Ahmedabad ring", "delivery", 23.0225, 72.5714, 35),
    ("NORTH", "Delhi NCR", "hub", 28.6139, 77.209, 60),
    ("NORTH", "Jaipur", "delivery", 26.9124, 75.7873, 30),
    ("SOUTH", "Bengaluru", "hub", 12.9716, 77.5946, 45),
    ("SOUTH", "Chennai port", "delivery", 13.0827, 80.2707, 40),
]

PLACES = [
    ("Bhiwandi mother warehouse", "PL-BHW", "warehouse", "WEST", "Bhiwandi", "Maharashtra", "421302", 19.2967, 73.0631),
    ("Taloja plant", "PL-TLJ", "plant", "WEST", "Taloja", "Maharashtra", "410208", 19.0728, 73.1046),
    ("Chakan distribution centre", "PL-CKN", "warehouse", "WEST", "Chakan", "Maharashtra", "410501", 18.7606, 73.8636),
    ("Aslali transport nagar", "PL-ASL", "hub", "WEST", "Ahmedabad", "Gujarat", "382427", 22.9195, 72.6236),
    ("Kundli logistics park", "PL-KND", "hub", "NORTH", "Sonipat", "Haryana", "131028", 28.8698, 77.1179),
    ("Sitapura industrial area", "PL-STP", "customer", "NORTH", "Jaipur", "Rajasthan", "302022", 26.7797, 75.8492),
    ("Nelamangala hub", "PL-NLM", "hub", "SOUTH", "Bengaluru", "Karnataka", "562123", 13.0994, 77.3936),
    ("Sriperumbudur DC", "PL-SPB", "customer", "SOUTH", "Chennai", "Tamil Nadu", "602105", 12.9675, 79.9436),
    ("Vashi HP fuel station", "PL-FUEL1", "fuel_station", "WEST", "Navi Mumbai", "Maharashtra", "400703", 19.0771, 73.0007),
]

VENDORS = [
    ("Shree Balaji Roadlines", "VN-001", "transporter", "Ahmedabad", "Gujarat", "24AAECS1429L1ZP"),
    ("Kisan Transport Company", "VN-002", "transporter", "Jaipur", "Rajasthan", "08AACCK9021M1Z4"),
    ("Sai Commission Agent", "VN-003", "broker", "Nagpur", "Maharashtra", ""),
    ("Ashok Leyland Service Centre", "VN-004", "workshop", "Pune", "Maharashtra", "27AAACA0000A1Z1"),
    ("MRF Tyre House", "VN-005", "tyre", "Chennai", "Tamil Nadu", "33AAACM1234B1ZQ"),
]

VEHICLES = [
    ("MH 04 JU 9182", "32 ft MXL container", 16000, "Tata Signa 4825.TK", 268400),
    ("MH 12 PQ 4407", "22 ft SXL", 9000, "Ashok Leyland Ecomet 1615", 142900),
    ("GJ 01 KT 7730", "20 ft open body", 7500, "Eicher Pro 2110", 98700),
    ("HR 55 AN 4021", "32 ft MXL container", 16000, "BharatBenz 3523R", 187300),
    ("KA 51 MN 6814", "32 ft MXL container", 16000, "Tata Prima 3530", 214600),
]

DRIVERS = [
    ("Ramesh Yadav", "+919820011223", "MH0320180001234", "Nashik", 22000, 600),
    ("Sandeep Kumar", "+919812233445", "HR5120170004567", "Rohtak", 21000, 550),
    ("Irfan Sheikh", "+919825566778", "GJ0120190007890", "Ahmedabad", 20500, 550),
    ("Vijay Raj", "+919845566990", "KA5120160002345", "Tumkur", 23000, 600),
]

RATE_CARDS = [
    ("Mumbai-Pune 32ft contract", "RC-MUMPUN", "WEST", "32 ft MXL container", "per_km", 2500, 48, 5),
    ("Gujarat lane per ton-km", "RC-GJTON", "WEST", "20 ft open body", "per_ton_km", 1200, 0, 5),
    ("NCR parcel per kg", "RC-NCRKG", "NORTH", "22 ft SXL", "per_kg", 500, 0, 12),
    ("South India FTL fixed", "RC-SOUTHFTL", "SOUTH", "32 ft MXL container", "per_trip", 42000, 0, 5),
]


class Command(BaseCommand):
    help = "Seed FleetOps demo data for Indian fleet operations"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing FleetOps demo records first")

    @transaction.atomic
    def handle(self, *args, **options):
        today = timezone.localdate()
        if options["reset"]:
            for model in (Waypoint, Order, FuelEntry, TripExpense, Issue, ComplianceDocument, MaintenanceSchedule,
                          ServiceRate, Fleet, Place, Zone, ServiceArea, Vendor):
                model.objects.all().delete()
            self.stdout.write("Cleared existing FleetOps records")

        areas = {}
        for name, code, states, latitude, longitude in SERVICE_AREAS:
            areas[code] = ServiceArea.objects.update_or_create(
                code=code, defaults={"name": name, "states": states, "description": f"Operating region centred on {name}"})[0]

        for area_code, name, zone_type, latitude, longitude, radius in ZONES:
            Zone.objects.update_or_create(service_area=areas[area_code], name=name, defaults={
                "zone_type": zone_type, "center_latitude": latitude, "center_longitude": longitude, "radius_km": radius})

        vendors = {}
        for name, code, vendor_type, city, state, gstin in VENDORS:
            vendors[code] = Vendor.objects.update_or_create(code=code, defaults={
                "name": name, "vendor_type": vendor_type, "city": city, "state": state, "gstin": gstin,
                "phone": "+9198200" + code[-5:].replace("-", ""), "payment_terms_days": 30})[0]

        places = {}
        for name, code, place_type, area_code, city, state, pincode, latitude, longitude in PLACES:
            places[code] = Place.objects.update_or_create(code=code, defaults={
                "name": name, "place_type": place_type, "service_area": areas[area_code], "city": city, "state": state,
                "pincode": pincode, "latitude": latitude, "longitude": longitude, "address": f"{name}, {city}, {state}",
                "loading_hours": "09:00-21:00"})[0]

        vehicles = {}
        for registration, vehicle_type, capacity, make_model, odometer in VEHICLES:
            vehicles[registration] = Vehicle.objects.update_or_create(registration_number=registration, defaults={
                "vehicle_type": vehicle_type, "capacity_kg": capacity, "make_model": make_model, "ownership": "owned",
                "status": "available", "fuel_type": "diesel", "current_odometer_km": odometer,
                "fastag_id": "FT" + registration.replace(" ", "")[-6:], "insurance_expiry": today + timedelta(days=95),
                "permit_expiry": today + timedelta(days=210)})[0]

        drivers = {}
        for name, phone, licence, home_city, salary, allowance in DRIVERS:
            drivers[name] = Driver.objects.update_or_create(licence_number=licence, defaults={
                "name": name, "phone": phone, "home_city": home_city, "monthly_salary": salary,
                "daily_allowance": allowance, "status": "available", "licence_expiry": today + timedelta(days=400),
                "date_of_joining": today - timedelta(days=900)})[0]

        fleets = {
            "FL-WEST": Fleet.objects.update_or_create(code="FL-WEST", defaults={
                "name": "West India owned fleet", "service_area": areas["WEST"], "manager": "Arjun Kapoor"})[0],
            "FL-ATT": Fleet.objects.update_or_create(code="FL-ATT", defaults={
                "name": "Attached vendor fleet", "service_area": areas["NORTH"], "vendor": vendors["VN-001"],
                "manager": "Suresh Patil"})[0],
        }
        fleets["FL-WEST"].vehicles.set([vehicles["MH 04 JU 9182"], vehicles["MH 12 PQ 4407"], vehicles["GJ 01 KT 7730"]])
        fleets["FL-WEST"].drivers.set([drivers["Ramesh Yadav"], drivers["Irfan Sheikh"]])
        fleets["FL-ATT"].vehicles.set([vehicles["HR 55 AN 4021"], vehicles["KA 51 MN 6814"]])
        fleets["FL-ATT"].drivers.set([drivers["Sandeep Kumar"], drivers["Vijay Raj"]])

        rates = {}
        for name, code, area_code, vehicle_type, rate_type, base, per_km, gst in RATE_CARDS:
            rates[code] = ServiceRate.objects.update_or_create(code=code, defaults={
                "name": name, "service_area": areas[area_code], "vehicle_type": vehicle_type, "rate_type": rate_type,
                "base_charge": base, "per_km_rate": per_km, "per_ton_km_rate": Decimal("6.50"),
                "per_kg_rate": Decimal("4.20"), "minimum_charge": 8000, "loading_charge": 1800, "unloading_charge": 1500,
                "halting_charge_per_day": 2500, "fuel_surcharge_percent": Decimal("3.50"), "gst_percent": gst,
                "effective_from": today - timedelta(days=30), "effective_to": today + timedelta(days=335)})[0]

        customer = Customer.objects.first() or Customer.objects.create(
            name="Tata Consumer Products", gstin="27AAACT2727Q1ZW", pan="AAACT2727Q", phone="+912266001100",
            email="logistics@tataconsumer.example", credit_limit=800000, kyc_status="verified")

        lanes = [
            ("PL-BHW", "PL-CKN", "RC-MUMPUN", "ftl", 149, 12400, "Packaged food cartons", "dispatched"),
            ("PL-TLJ", "PL-ASL", "RC-GJTON", "ftl", 522, 9800, "Paint drums", "in_transit"),
            ("PL-KND", "PL-STP", "RC-NCRKG", "ptl", 288, 4200, "Electrical fittings", "created"),
            ("PL-NLM", "PL-SPB", "RC-SOUTHFTL", "ftl", 347, 15200, "White goods", "completed"),
        ]
        orders = []
        for index, (pickup, dropoff, rate_code, order_type, distance, weight, payload, status) in enumerate(lanes, start=1):
            order, _ = Order.objects.update_or_create(number=f"ORD-DEMO-{index:03d}", defaults={
                "customer": customer, "order_type": order_type, "service_rate": rates[rate_code],
                "pickup": places[pickup], "dropoff": places[dropoff], "distance_km": distance, "weight_kg": weight,
                "payload_description": payload, "packages": 240 + index * 30, "declared_value": 450000,
                "scheduled_at": timezone.now() + timedelta(hours=index * 6), "status": status,
                "payment_mode": "to_pay", "eway_bill_number": f"2712345678{index:02d}"})
            order.price_from_rate_card()
            if status in ("dispatched", "in_transit", "completed"):
                order.driver = list(drivers.values())[index % len(drivers)]
                order.vehicle = list(vehicles.values())[index % len(vehicles)]
                order.save(update_fields=["driver", "vehicle"])
            for sequence, (place_code, waypoint_type) in enumerate([(pickup, "pickup"), (dropoff, "drop")], start=1):
                Waypoint.objects.update_or_create(order=order, sequence=sequence, defaults={
                    "place": places[place_code], "waypoint_type": waypoint_type,
                    "planned_arrival": timezone.now() + timedelta(hours=sequence * 8),
                    "status": "completed" if status == "completed" else "pending"})
            if not order.activities.exists():
                order.log(order.status, "ORDER_CREATED", f"Seeded demo consignment for {customer.name}",
                          city=places[pickup].city)
            orders.append(order)

        for index, (registration, vehicle) in enumerate(vehicles.items()):
            # Two fills 1,000 km apart on 260 litres works out to a realistic 3.8 km/l for a loaded 32 ft truck.
            base_odometer = vehicle.current_odometer_km - 2000
            for fill in range(2):
                FuelEntry.objects.update_or_create(
                    vehicle=vehicle, invoice_number=f"FUEL-{registration[-4:]}-{fill}", defaults={
                        "driver": list(drivers.values())[index % len(drivers)],
                        "entry_date": timezone.localdate() - timedelta(days=20 - fill * 10),
                        "odometer_km": base_odometer + fill * 1000, "volume_litres": Decimal("260.50"),
                        "rate_per_litre": Decimal("94.20"), "payment_method": "fuel_card",
                        "station_name": "HP Vashi highway outlet"})

        expense_plan = [("toll", 3200, "fastag"), ("driver_allowance", 1800, "company"), ("loading", 1500, "driver"),
                        ("unloading", 1200, "driver"), ("parking", 350, "driver"), ("repair", 4800, "company")]
        for order in orders:
            for category, amount, paid_by in expense_plan:
                TripExpense.objects.update_or_create(order=order, category=category, defaults={
                    "vehicle": order.vehicle, "driver": order.driver, "amount": amount, "paid_by": paid_by,
                    "expense_date": timezone.localdate() - timedelta(days=3), "status": "approved",
                    "receipt_number": f"RCPT-{order.number[-3:]}-{category[:3].upper()}"})

        Issue.objects.update_or_create(number="ISS-DEMO-001", defaults={
            "vehicle": vehicles["MH 12 PQ 4407"], "driver": drivers["Irfan Sheikh"], "issue_type": "tyre",
            "priority": "high", "description": "Rear left tyre showing sidewall cut near Khed Shivapuri",
            "location_text": "NH-48 Khed Shivapuri", "latitude": 18.8574, "longitude": 73.7092, "status": "open"})
        Issue.objects.update_or_create(number="ISS-DEMO-002", defaults={
            "vehicle": vehicles["KA 51 MN 6814"], "driver": drivers["Vijay Raj"], "issue_type": "delay",
            "priority": "medium", "description": "Held at Krishnagiri check post for e-way bill verification",
            "location_text": "Krishnagiri check post", "status": "in_progress"})

        document_plan = [("rc", 3650), ("insurance", 95), ("fitness", 260), ("permit_national", 210), ("puc", 24), ("fastag", 400)]
        for vehicle in vehicles.values():
            for document_type, days in document_plan:
                ComplianceDocument.objects.update_or_create(vehicle=vehicle, document_type=document_type, defaults={
                    "number": f"{document_type.upper()}-{vehicle.registration_number.replace(' ', '')}",
                    "issuing_authority": "RTO Maharashtra", "issue_date": today - timedelta(days=365),
                    "expiry_date": today + timedelta(days=days), "reminder_days": 30})
        for driver in drivers.values():
            ComplianceDocument.objects.update_or_create(driver=driver, document_type="licence", defaults={
                "number": driver.licence_number, "issuing_authority": "RTO", "expiry_date": driver.licence_expiry,
                "reminder_days": 45})

        for index, vehicle in enumerate(vehicles.values()):
            MaintenanceSchedule.objects.update_or_create(vehicle=vehicle, task="Engine oil and filter change", defaults={
                "interval_km": 20000, "interval_days": 180, "last_service_km": max(vehicle.current_odometer_km - 19000, 0),
                "last_service_date": today - timedelta(days=150)})
            # Every other truck is deliberately past its tyre rotation so the "due" alerts have something to show.
            overdue = index % 2 == 0
            MaintenanceSchedule.objects.update_or_create(vehicle=vehicle, task="Tyre rotation and alignment", defaults={
                "interval_km": 15000, "interval_days": 120,
                "last_service_km": max(vehicle.current_odometer_km - (15600 if overdue else 9000), 0),
                "last_service_date": today - timedelta(days=125 if overdue else 70)})

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {ServiceArea.objects.count()} service areas, {Zone.objects.count()} zones, "
            f"{Place.objects.count()} places, {Vendor.objects.count()} vendors, {Fleet.objects.count()} fleets, "
            f"{ServiceRate.objects.count()} rate cards, {Order.objects.count()} orders, "
            f"{FuelEntry.objects.count()} fuel entries, {TripExpense.objects.count()} expenses, "
            f"{ComplianceDocument.objects.count()} compliance documents."))
