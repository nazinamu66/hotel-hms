# Generated for the unified supplier payment workflow.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0013_alter_account_slug_and_more"),
        ("inventory", "0050_product_equipment_type"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SupplierPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_type", models.CharField(choices=[("PO", "Purchase order"), ("DIRECT", "Direct purchase"), ("LABOUR", "Outsourced maintenance labour")], max_length=12)),
                ("source_id", models.PositiveBigIntegerField()),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("payment_date", models.DateField()),
                ("reference", models.CharField(blank=True, max_length=100)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("hotel", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="supplier_payments", to="inventory.hotel")),
                ("journal_entry", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="supplier_payments", to="accounting.journalentry")),
                ("payment_account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="supplier_payments", to="accounting.account")),
                ("supplier", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payments", to="inventory.supplier")),
            ],
            options={"ordering": ["-payment_date", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="supplierpayment",
            constraint=models.UniqueConstraint(condition=~models.Q(reference=""), fields=("hotel", "reference"), name="unique_supplier_payment_reference_per_hotel"),
        ),
    ]
