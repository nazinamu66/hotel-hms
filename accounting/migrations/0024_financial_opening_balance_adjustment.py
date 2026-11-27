from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0023_merge_gym_salon_revenue_accounts"),
        ("inventory", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FinancialOpeningBalanceAdjustment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("reason", models.TextField()),
                ("reference", models.CharField(blank=True, max_length=100)),
                ("status", models.CharField(choices=[("PENDING", "Awaiting approval"), ("APPROVED", "Approved and posted"), ("REJECTED", "Rejected")], default="PENDING", max_length=12)),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("review_note", models.TextField(blank=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="opening_balance_adjustments", to="accounting.account")),
                ("hotel", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="financial_opening_adjustments", to="inventory.hotel")),
                ("journal_entry", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="opening_balance_adjustment", to="accounting.journalentry")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="requested_opening_adjustments", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_opening_adjustments", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-requested_at"]},
        ),
        migrations.AddConstraint(
            model_name="financialopeningbalanceadjustment",
            constraint=models.CheckConstraint(condition=models.Q(("amount__gt", 0)), name="opening_adjustment_amount_positive"),
        ),
    ]
