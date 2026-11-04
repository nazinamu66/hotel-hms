import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("laundry", "0008_laundryprocessing_linen_transaction"),
        ("linen", "0014_linentransaction_discrepancy_location"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="laundryreceipt",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("RECEIVED", "Received"),
                    ("DISCREPANCY_PENDING", "Discrepancy Pending Review"),
                    ("DISCREPANCY_RESOLVED", "Discrepancy Resolved"),
                    ("CANCELLED", "Cancelled"),
                ],
                default="PENDING",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="LaundryReceiptDiscrepancy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("condition", models.CharField(choices=[("CLEAN", "Clean"), ("DIRTY", "Dirty")], max_length=10)),
                ("quantity", models.PositiveIntegerField()),
                ("status", models.CharField(choices=[("OPEN", "Open"), ("RESOLVED", "Resolved")], default="OPEN", max_length=12)),
                ("resolution", models.CharField(blank=True, choices=[("RECOVERED", "Recovered and received"), ("LOST", "Confirmed lost"), ("DAMAGED", "Confirmed damaged")], max_length=12)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("resolution_note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("receipt", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="discrepancies", to="laundry.laundryreceipt")),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="laundry_discrepancies_resolved", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["created_at", "id"],
                "constraints": [
                    models.UniqueConstraint(fields=("receipt", "condition"), name="unique_receipt_condition_discrepancy"),
                ],
            },
        ),
    ]
