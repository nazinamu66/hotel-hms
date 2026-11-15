from django.db import migrations, models
from decimal import Decimal


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0031_payment_allocation_tracking_and_unapplied_credit"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="applied_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="Amount of this receipt applied to the folio; excess is held as guest credit.",
                max_digits=12,
            ),
        ),
    ]
