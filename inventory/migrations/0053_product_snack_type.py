from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0052_organization_logo"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="product_type",
            field=models.CharField(
                choices=[
                    ("RAW", "Raw Material"),
                    ("FOOD", "Prepared Food"),
                    ("SNACK", "Prepared Snack"),
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
