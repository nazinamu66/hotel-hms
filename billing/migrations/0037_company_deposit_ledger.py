# Corporate deposit ledger, separate from individual guest deposits.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from decimal import Decimal


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0018_complimentary_meal_entry_type"),
        ("billing", "0036_complimentary_room_night"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyDeposit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("balance", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="deposits", to="billing.company")),
                ("hotel", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="company_deposits", to="inventory.hotel")),
            ],
        ),
        migrations.AddConstraint(
            model_name="companydeposit",
            constraint=models.UniqueConstraint(fields=("hotel", "company"), name="unique_company_deposit_per_hotel"),
        ),
        migrations.CreateModel(
            name="CompanyDepositTransaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("transaction_type", models.CharField(choices=[("RECEIVED", "Deposit Received"), ("APPLIED", "Applied to Company Invoice"), ("REFUNDED", "Deposit Refunded"), ("ADJUSTMENT", "Balance Adjustment")], max_length=12)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("payment_method", models.CharField(blank=True, choices=[("CASH", "Cash"), ("POS", "POS"), ("TRANSFER", "Bank Transfer")], max_length=12)),
                ("reference", models.CharField(blank=True, max_length=100)),
                ("note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("bank_account", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="company_deposit_transactions", to="accounting.bankaccount")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="company_deposit_transactions_created", to=settings.AUTH_USER_MODEL)),
                ("deposit", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="transactions", to="billing.companydeposit")),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="companydeposittransaction",
            constraint=models.CheckConstraint(condition=models.Q(("amount__gt", Decimal("0.00"))), name="company_deposit_transaction_amount_positive"),
        ),
    ]
