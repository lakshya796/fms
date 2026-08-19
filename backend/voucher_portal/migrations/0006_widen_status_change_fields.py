from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("voucher_portal", "0005_two_stage_approval_and_visibility"),
    ]

    operations = [
        migrations.AlterField(
            model_name="statuschange",
            name="from_status",
            field=models.CharField(blank=True, max_length=24),
        ),
        migrations.AlterField(
            model_name="statuschange",
            name="to_status",
            field=models.CharField(max_length=24),
        ),
    ]
