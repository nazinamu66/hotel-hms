from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("kitchen", "0019_directpurchaseitem_quantity_received_and_status"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="directpurchase",
            name="status",
            field=models.CharField(
                choices=[
                    ("APPROVED", "Approved"),
                    ("RECONCILIATION_PENDING", "Awaiting Reconciliation"),
                    ("PAID", "Paid"),
                    ("PARTIALLY_RECEIVED", "Partially Received"),
                    ("RECEIVED", "Received"),
                    ("REJECTED", "Rejected"),
                ],
                default="APPROVED",
                max_length=24,
            ),
        ),
        migrations.AddField(model_name="directpurchase", name="purchase_date", field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name="directpurchase", name="payment_source", field=models.CharField(blank=True, choices=[("HOTEL_FUNDS", "Hotel funds"), ("STAFF_FUNDS", "Staff funds"), ("UNPAID", "Not yet paid")], max_length=20)),
        migrations.AddField(model_name="directpurchase", name="receipt_reference", field=models.CharField(blank=True, max_length=120)),
        migrations.AddField(model_name="directpurchase", name="urgent_reason", field=models.TextField(blank=True)),
        migrations.AddField(model_name="directpurchase", name="urgent_purchase", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="directpurchase", name="goods_already_received", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="directpurchase", name="reconciliation_note", field=models.TextField(blank=True)),
        migrations.AddField(model_name="directpurchase", name="reconciled_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reconciled_direct_purchases", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="directpurchase", name="reconciled_at", field=models.DateTimeField(blank=True, null=True)),
    ]
