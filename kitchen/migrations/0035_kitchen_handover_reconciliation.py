# Generated for Kitchen handover discrepancy investigation.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0054_purchase_receipt_ledger"),
        ("kitchen", "0034_kitchenhandoverline_system_quantity"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="KitchenHandoverReconciliation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(
                    choices=[
                        ("INVESTIGATING", "Under Head Chef investigation"),
                        ("SUBMITTED", "Submitted for management approval"),
                        ("APPROVED", "Approved for adjustment"),
                        ("REJECTED", "Rejected by management"),
                        ("ADJUSTED", "Inventory adjustment posted"),
                        ("CLOSED", "Closed without adjustment"),
                    ],
                    default="INVESTIGATING",
                    max_length=16,
                )),
                ("opened_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("investigation_notes", models.TextField(blank=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("approval_note", models.TextField(blank=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("department", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="handover_reconciliations",
                    to="inventory.department",
                )),
                ("handover", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="reconciliation",
                    to="kitchen.kitchenshifthandover",
                )),
                ("opened_by", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="opened_kitchen_reconciliations",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("reviewed_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="reviewed_kitchen_reconciliations",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["-opened_at", "-pk"]},
        ),
        migrations.CreateModel(
            name="KitchenHandoverReconciliationLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("verified_quantity", models.DecimalField(
                    blank=True, decimal_places=3, max_digits=12, null=True,
                )),
                ("investigation_note", models.TextField(blank=True)),
                ("adjustment_cost", models.DecimalField(
                    blank=True, decimal_places=2, max_digits=14, null=True,
                )),
                ("adjustment_movement", models.OneToOneField(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="kitchen_handover_reconciliation_line",
                    to="inventory.stockmovement",
                )),
                ("handover_line", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="reconciliation_line",
                    to="kitchen.kitchenshifthandoverline",
                )),
                ("reconciliation", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="lines",
                    to="kitchen.kitchenhandoverreconciliation",
                )),
            ],
            options={"ordering": ["handover_line__location__name", "handover_line__item_name"]},
        ),
    ]
