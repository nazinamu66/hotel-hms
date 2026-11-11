from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_cashhandover_resolution_amount_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cashhandover",
            name="custody_type",
            field=models.CharField(
                choices=[
                    ("SHIFT", "Shift Handover"),
                    ("CUSTODY", "Cash Custody"),
                    ("PARTIAL", "Partial Cash Handover"),
                ],
                default="CUSTODY",
                max_length=20,
            ),
        ),
    ]
