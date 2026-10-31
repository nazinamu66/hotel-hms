import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import decimal


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("accounting", "0016_opening_balance_equity"),
        ("inventory", "0053_stockcostlayer_source_reference"),
        ("linen", "0013_roomlinensetup"),
        ("maintenance", "0009_equipment_and_locations"),
        ("rooms", "0012_alter_amenity_hotel"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="OpeningStockBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference", models.CharField(max_length=60)),
                ("opening_date", models.DateField()),
                ("status", models.CharField(choices=[("DRAFT", "Draft"), ("POSTED", "Posted")], default="DRAFT", max_length=10)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("posted_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="opening_stock_batches_created", to=settings.AUTH_USER_MODEL)),
                ("hotel", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="opening_stock_batches", to="inventory.hotel")),
                ("journal_entry", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="opening_stock_batch", to="accounting.journalentry")),
                ("posted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="opening_stock_batches_posted", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-opening_date", "-id"]},
        ),
        migrations.CreateModel(
            name="OpeningStockLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("line_type", models.CharField(choices=[("STOCK", "Department stock"), ("LINEN", "Linen"), ("EQUIPMENT", "Equipment / fixed asset")], max_length=12)),
                ("quantity", models.DecimalField(decimal_places=3, default=decimal.Decimal("0.000"), max_digits=12)),
                ("unit_cost", models.DecimalField(decimal_places=4, default=decimal.Decimal("0.0000"), max_digits=12)),
                ("linen_location", models.CharField(blank=True, choices=[("STORE", "Linen Store"), ("HOUSEKEEPING", "Housekeeping"), ("LAUNDRY", "Laundry")], max_length=20)),
                ("linen_condition", models.CharField(blank=True, choices=[("CLEAN", "Clean"), ("DIRTY", "Dirty")], max_length=10)),
                ("asset_name", models.CharField(blank=True, max_length=150)),
                ("serial_number", models.CharField(blank=True, max_length=100)),
                ("manufacturer", models.CharField(blank=True, max_length=100)),
                ("model_number", models.CharField(blank=True, max_length=100)),
                ("asset_condition", models.CharField(blank=True, choices=[("NEW", "New"), ("GOOD", "Good"), ("FAIR", "Fair"), ("POOR", "Poor"), ("DAMAGED", "Damaged")], max_length=12)),
                ("asset_purchase_date", models.DateField(blank=True, null=True)),
                ("asset_value", models.DecimalField(decimal_places=2, default=decimal.Decimal("0.00"), max_digits=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("asset_product", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="opening_equipment_lines", to="inventory.product")),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="opening_stock.openingstockbatch")),
                ("department", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="opening_stock_lines", to="inventory.department")),
                ("linen_item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="opening_stock_lines", to="linen.linenitem")),
                ("maintenance_location", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="opening_equipment_lines", to="maintenance.maintenancelocation")),
                ("product", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="opening_stock_lines", to="inventory.product")),
                ("room", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="opening_equipment_lines", to="rooms.room")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddConstraint(
            model_name="openingstockbatch",
            constraint=models.UniqueConstraint(fields=("hotel", "reference"), name="unique_opening_batch_reference_per_hotel"),
        ),
    ]
