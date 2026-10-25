# Add the Equipment product classification.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0049_supplier_is_active"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="product_type",
            field=models.CharField(
                choices=[
                    ("RAW", "Raw Material"),
                    ("FOOD", "Prepared Food"),
                    ("DRINK", "Beverage"),
                    ("HOUSEKEEPING", "Housekeeping Supply"),
                    ("LAUNDRY", "Laundry Supply"),
                    ("BOUTIQUE", "Boutique Item"),
                    ("EQUIPMENT", "Equipment"),
                    ("SERVICE", "Service"),
                ],
                default="RAW",
                max_length=20,
            ),
        ),
    ]
