from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0053_stockcostlayer_source_reference"),
        ("kitchen", "0030_kitchenrestrictedstockrecord"),
    ]

    operations = [
        migrations.CreateModel(
            name="KitchenRestrictedStockLayer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_reference", models.CharField(blank=True, db_index=True, default="", max_length=100)),
                ("quantity_received", models.DecimalField(decimal_places=3, max_digits=12)),
                ("quantity_remaining", models.DecimalField(decimal_places=3, max_digits=12)),
                ("unit_cost", models.DecimalField(decimal_places=4, max_digits=12)),
                ("received_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("department", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="restricted_stock_layers", to="inventory.department")),
                ("location", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="restricted_stock_layers", to="kitchen.kitchenstoragelocation")),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="restricted_stock_layers", to="inventory.product")),
                ("source_cost_layer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="restricted_stock_layers", to="inventory.stockcostlayer")),
            ],
            options={
                "ordering": ["received_at", "pk"],
                "indexes": [
                    models.Index(fields=["product", "location", "quantity_remaining", "received_at"], name="kitch_restrict_fifo_idx"),
                ],
            },
        ),
    ]
