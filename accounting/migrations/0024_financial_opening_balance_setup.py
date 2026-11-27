from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0023_merge_gym_salon_revenue_accounts"),
        ("inventory", "0058_alter_hotelfeature_feature"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FinancialOpeningBalanceSetup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("opening_date", models.DateField()),
                ("balances", models.JSONField(default=dict)),
                ("posted_at", models.DateTimeField(auto_now_add=True)),
                ("hotel", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="financial_opening_balance_setup", to="inventory.hotel")),
                ("posted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="financial_opening_balance_setups", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-posted_at"],
            },
        ),
    ]
