from decimal import Decimal

from django.db import migrations, models


def preserve_existing_direct_receipts(apps, schema_editor):
    MaintenanceMaterial = apps.get_model("maintenance", "MaintenanceMaterial")
    MaintenanceMaterial.objects.filter(source="DIRECT").update(
        received_quantity=models.F("quantity")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0013_direct_purchase_custom_item"),
    ]

    operations = [
        migrations.AddField(
            model_name="maintenancematerial",
            name="received_quantity",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=12),
        ),
        migrations.RunPython(
            preserve_existing_direct_receipts,
            migrations.RunPython.noop,
        ),
    ]
