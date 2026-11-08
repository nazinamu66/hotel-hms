import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def backfill_historical_receipts(apps, schema_editor):
    PurchaseOrder = apps.get_model("inventory", "PurchaseOrder")
    PurchaseItem = apps.get_model("inventory", "PurchaseItem")
    PurchaseReceipt = apps.get_model("inventory", "PurchaseReceipt")
    PurchaseReceiptItem = apps.get_model("inventory", "PurchaseReceiptItem")
    database = schema_editor.connection.alias

    for po in PurchaseOrder.objects.using(database).filter(status="RECEIVED").iterator():
        receipt = PurchaseReceipt.objects.using(database).create(
            purchase_order_id=po.pk,
            received_at=po.received_at or po.created_at or timezone.now(),
            notes="Migrated historical full receipt; original receiving user was not recorded.",
        )
        items = PurchaseItem.objects.using(database).filter(purchase_order_id=po.pk)
        for item in items.iterator():
            if item.purchase_quantity > 0:
                PurchaseReceiptItem.objects.using(database).create(
                    receipt_id=receipt.pk,
                    purchase_item_id=item.pk,
                    quantity=item.purchase_quantity,
                    unit_cost=item.unit_cost,
                )


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0053_stockcostlayer_source_reference"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="purchaseorder",
            name="status",
            field=models.CharField(
                choices=[
                    ("DRAFT", "Draft"),
                    ("SUBMITTED", "Submitted"),
                    ("APPROVED", "Approved"),
                    ("PARTIALLY_RECEIVED", "Partially Received"),
                    ("RECEIVED", "Received"),
                    ("REJECTED", "Rejected"),
                ],
                default="DRAFT",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="PurchaseReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("received_at", models.DateTimeField(default=timezone.now)),
                ("notes", models.TextField(blank=True)),
                ("purchase_order", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="receipts",
                    to="inventory.purchaseorder",
                )),
                ("received_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="purchase_receipts",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["received_at", "id"]},
        ),
        migrations.CreateModel(
            name="PurchaseReceiptItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.PositiveIntegerField()),
                ("unit_cost", models.DecimalField(decimal_places=2, max_digits=10)),
                ("purchase_item", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="receipt_lines",
                    to="inventory.purchaseitem",
                )),
                ("receipt", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="items",
                    to="inventory.purchasereceipt",
                )),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddConstraint(
            model_name="purchasereceiptitem",
            constraint=models.UniqueConstraint(
                fields=("receipt", "purchase_item"),
                name="unique_purchase_item_per_receipt",
            ),
        ),
        migrations.RunPython(backfill_historical_receipts, migrations.RunPython.noop),
    ]
