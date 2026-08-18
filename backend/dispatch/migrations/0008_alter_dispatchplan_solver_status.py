from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dispatch", "0007_alter_dispatchtask_status_scenarioprofile_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="dispatchplan",
            name="solver_status",
            field=models.CharField(blank=True, max_length=240),
        ),
    ]
