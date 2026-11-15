from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0030_charge_allocation_eligibility"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="allocation_tracking_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Folio balance uses applied allocations rather than the full receipt amount.",
            ),
        ),
        migrations.AlterField(
            model_name="guestdeposittransaction",
            name="transaction_type",
            field=models.CharField(
                choices=[
                    ("RECEIVED", "Deposit Received"),
                    ("APPLIED", "Deposit Applied"),
                    ("UNAPPLIED_CREDIT", "Unapplied Folio Credit"),
                    ("REFUNDED", "Deposit Refunded"),
                    ("FORFEITED", "Reservation Guarantee Forfeited"),
                ],
                max_length=20,
            ),
        ),
    ]
