import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0047_folio_checkout_authorization_and_balance_waiver"),
        ("salon", "0002_salonshift"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="salon_shift",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="payments", to="salon.salonshift"),
        ),
    ]
