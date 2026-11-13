# Generated for partial Restaurant POS refund payouts.
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0001_initial"),
        ("billing", "0027_refund_execution_audit"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="refundrequest",
            name="execution_amount",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12),
        ),
        migrations.CreateModel(
            name="RefundExecution",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("forfeited_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("reference", models.CharField(max_length=100)),
                ("note", models.TextField(blank=True)),
                ("executed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="restaurant_refund_payouts", to="accounting.account")),
                ("executed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="restaurant_refund_payouts", to=settings.AUTH_USER_MODEL)),
                ("refund_request", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="payout", to="billing.refundrequest")),
            ],
        ),
    ]
