from decimal import Decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0028_partial_pos_refund_payout"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ChargePaymentAllocation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("allocation_type", models.CharField(
                    choices=[("FIFO", "Folio FIFO"), ("POS_DIRECT", "POS order direct")],
                    default="FIFO", max_length=20,
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("charge", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="payment_allocations", to="billing.charge",
                )),
                ("payment", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="charge_allocations", to="billing.payment",
                )),
                ("created_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="charge_payment_allocations_created", to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="chargepaymentallocation",
            constraint=models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0.00")),
                name="charge_payment_allocation_amount_positive",
            ),
        ),
    ]
