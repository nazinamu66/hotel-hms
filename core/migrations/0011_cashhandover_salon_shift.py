import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_cashhandover_partial_type"),
        ("salon", "0002_salonshift"),
    ]

    operations = [
        migrations.AddField(
            model_name="cashhandover",
            name="salon_shift",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cash_handovers", to="salon.salonshift"),
        ),
    ]
