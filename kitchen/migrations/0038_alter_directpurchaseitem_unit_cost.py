from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kitchen", "0037_merge_20261110"),
    ]

    operations = [
        migrations.AlterField(
            model_name="directpurchaseitem",
            name="unit_cost",
            field=models.DecimalField(
                decimal_places=4,
                help_text="Cost per product base unit, derived from the supplier purchase-unit price.",
                max_digits=12,
            ),
        ),
    ]
