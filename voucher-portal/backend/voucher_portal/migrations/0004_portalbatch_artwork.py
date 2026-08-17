from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("voucher_portal", "0003_portalbatch_approved_at_portalbatch_approved_by_and_more")]

    operations = [
        migrations.AddField(
            model_name="portalbatch",
            name="artwork",
            field=models.ImageField(
                blank=True,
                help_text="Optional batch-specific artwork that overrides the selected template artwork",
                null=True,
                upload_to="voucher-portal/batches/",
            ),
        ),
    ]
