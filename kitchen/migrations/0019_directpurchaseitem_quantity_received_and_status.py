from decimal import Decimal

from django.db import migrations, models


def mark_existing_received_items(apps, schema_editor):
    DirectPurchase = apps.get_model("kitchen", "DirectPurchase")
    DirectPurchaseItem = apps.get_model("kitchen", "DirectPurchaseItem")
    received_purchase_ids = DirectPurchase.objects.filter(status="RECEIVED").values_list("pk", flat=True)
    DirectPurchaseItem.objects.filter(purchase_id__in=received_purchase_ids).update(
        quantity_received=models.F("quantity")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("kitchen", "0018_recipe_version_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="directpurchaseitem",
            name="quantity_received",
            field=models.DecimalField(
                decimal_places=3,
                default=Decimal("0.000"),
                help_text="Cumulative quantity received in the product's base unit.",
                max_digits=10,
            ),
        ),
        migrations.AlterField(
            model_name="directpurchase",
            name="status",
            field=models.CharField(
                choices=[
                    ("APPROVED", "Approved"),
                    ("PAID", "Paid"),
                    ("PARTIALLY_RECEIVED", "Partially Received"),
                    ("RECEIVED", "Received"),
                    ("REJECTED", "Rejected"),
                ],
                default="APPROVED",
                max_length=20,
            ),
        ),
        migrations.RunPython(mark_existing_received_items, migrations.RunPython.noop),
    ]
