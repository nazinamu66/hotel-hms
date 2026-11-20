from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0033_charge_billing_party_company_groupreservation_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="guestdeposittransaction",
            name="payment_method",
            field=models.CharField(
                blank=True,
                choices=[
                    ("CASH", "Cash"),
                    ("POS", "POS"),
                    ("TRANSFER", "Bank Transfer"),
                ],
                default="",
                max_length=20,
            ),
        ),
    ]
