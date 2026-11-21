from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("maintenance", "0011_maintenanceschedulecycle"),
        ("inventory", "0033_charge_billing_party_company_groupreservation_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MaintenanceDirectPurchaseRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("requested_quantity", models.PositiveIntegerField()),
                ("reason", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("PENDING", "Pending approval"), ("REJECTED", "Rejected"), ("APPROVED", "Approved — purchase order required"), ("PARTIALLY_RECEIVED", "Partially received"), ("FULFILLED", "Fulfilled")], default="PENDING", max_length=24)),
                ("manager_note", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="maintenance_direct_purchase_requests", to="inventory.product")),
                ("purchase_order", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="maintenance_direct_request", to="inventory.purchaseorder")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="maintenance_direct_purchase_requests", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_maintenance_purchase_requests", to=settings.AUTH_USER_MODEL)),
                ("ticket", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="direct_purchase_requests", to="maintenance.maintenanceticket")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
