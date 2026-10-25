# Preserve three-decimal base-unit quantities across stock and movement records.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0050_product_equipment_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="stock",
            name="quantity",
            field=models.DecimalField(decimal_places=3, default=0, max_digits=12),
        ),
        migrations.AlterField(
            model_name="stockcostlayer",
            name="quantity_received",
            field=models.DecimalField(decimal_places=3, max_digits=12),
        ),
        migrations.AlterField(
            model_name="stockcostlayer",
            name="quantity_remaining",
            field=models.DecimalField(decimal_places=3, max_digits=12),
        ),
        migrations.AlterField(
            model_name="stockmovement",
            name="quantity",
            field=models.DecimalField(decimal_places=3, max_digits=12),
        ),
        migrations.AlterField(
            model_name="stockmovementcostallocation",
            name="quantity",
            field=models.DecimalField(decimal_places=3, max_digits=12),
        ),
        migrations.AlterField(
            model_name="stocktransfer",
            name="quantity",
            field=models.DecimalField(decimal_places=3, max_digits=12),
        ),
        migrations.AlterField(
            model_name="stockout",
            name="quantity",
            field=models.DecimalField(decimal_places=3, max_digits=12),
        ),
        migrations.AlterField(
            model_name="stockadjustment",
            name="quantity",
            field=models.DecimalField(decimal_places=3, max_digits=12),
        ),
    ]
