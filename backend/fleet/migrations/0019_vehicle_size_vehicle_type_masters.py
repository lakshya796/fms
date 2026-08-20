from django.db import migrations, models


DEFAULT_SIZES = [
    ("14 ft", 3500),
    ("17 ft", 5000),
    ("19 ft", 7000),
    ("20 ft SXL", 7500),
    ("22 ft SXL", 9000),
    ("24 ft", 11000),
    ("32 ft SXL", 16000),
    ("32 ft MXL", 18000),
]

DEFAULT_TYPES = [
    ("Dry", "dry", "closed"),
    ("Reefer", "chiller", "reefer"),
    ("Frozen reefer", "frozen", "reefer"),
    ("Multi-compartment reefer", "multi", "reefer"),
]


def seed_vehicle_masters(apps, schema_editor):
    VehicleSize = apps.get_model("fleet", "VehicleSize")
    VehicleType = apps.get_model("fleet", "VehicleType")

    discovered = {}
    Vehicle = apps.get_model("fleet", "Vehicle")
    for name, capacity in Vehicle.objects.exclude(vehicle_type="").values_list("vehicle_type", "capacity_kg"):
        key = name.strip().lower()
        if key:
            discovered[key] = (name.strip(), max(int(capacity or 0), discovered.get(key, ("", 0))[1]))

    for model_name, field_name in [
        ("Indent", "vehicle_type"),
        ("ServiceRate", "vehicle_type"),
        ("VehicleHire", "outside_vehicle_type"),
        ("VendorLaneRate", "vehicle_type"),
    ]:
        Model = apps.get_model("fleet", model_name)
        for name in Model.objects.exclude(**{field_name: ""}).values_list(field_name, flat=True).distinct():
            key = name.strip().lower()
            if key and key not in discovered:
                discovered[key] = (name.strip(), 0)

    for name, capacity in DEFAULT_SIZES:
        discovered.setdefault(name.lower(), (name, capacity))
    for position, (name, capacity) in enumerate(sorted(discovered.values(), key=lambda row: row[0].lower()), 1):
        VehicleSize.objects.get_or_create(
            name=name,
            defaults={"default_capacity_kg": capacity, "sort_order": position, "status": "active"},
        )

    for position, (name, temperature_class, body_type) in enumerate(DEFAULT_TYPES, 1):
        VehicleType.objects.get_or_create(
            name=name,
            defaults={
                "temperature_class": temperature_class,
                "body_type": body_type,
                "sort_order": position,
                "status": "active",
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("fleet", "0018_pod_one_per_order"),
    ]

    operations = [
        migrations.CreateModel(
            name="VehicleSize",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=60, unique=True)),
                ("default_capacity_kg", models.PositiveIntegerField(default=0)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("status", models.CharField(choices=[("active", "Active"), ("inactive", "Inactive")], default="active", max_length=10)),
            ],
            options={"ordering": ["sort_order", "name"]},
        ),
        migrations.CreateModel(
            name="VehicleType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=60, unique=True)),
                ("temperature_class", models.CharField(choices=[("dry", "Dry"), ("chiller", "Chiller"), ("frozen", "Frozen"), ("multi", "Multi-compartment")], default="dry", max_length=10)),
                ("body_type", models.CharField(choices=[("open", "Open"), ("closed", "Closed"), ("container", "Container"), ("tanker", "Tanker"), ("flatbed", "Flatbed"), ("reefer", "Reefer")], default="closed", max_length=20)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("status", models.CharField(choices=[("active", "Active"), ("inactive", "Inactive")], default="active", max_length=10)),
            ],
            options={"ordering": ["sort_order", "name"]},
        ),
        migrations.AddField(
            model_name="servicerate",
            name="temperature_class",
            field=models.CharField(choices=[("dry", "Dry"), ("chiller", "Chiller"), ("frozen", "Frozen"), ("multi", "Multi-compartment")], default="dry", max_length=10),
        ),
        migrations.AddField(
            model_name="order",
            name="vehicle_type",
            field=models.CharField(blank=True, max_length=60),
        ),
        migrations.RunPython(seed_vehicle_masters, migrations.RunPython.noop),
    ]
