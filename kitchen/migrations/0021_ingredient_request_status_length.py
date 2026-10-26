from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("kitchen", "0020_urgent_purchase_reconciliation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ingredientrestockrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("APPROVED", "Approved"),
                    ("RECONCILIATION_PENDING", "Awaiting Reconciliation"),
                    ("PARTIALLY_ISSUED", "Partially Issued"),
                    ("REJECTED", "Rejected"),
                    ("RECEIVED", "Received"),
                ],
                default="PENDING",
                max_length=24,
            ),
        ),
    ]
