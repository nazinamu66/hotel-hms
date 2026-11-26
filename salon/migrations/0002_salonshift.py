import decimal
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("salon", "0001_initial"),
        ("inventory", "0058_alter_hotelfeature_feature"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SalonShift",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("opened_at", models.DateTimeField(auto_now_add=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("opening_cash", models.DecimalField(decimal_places=2, default=decimal.Decimal("0.00"), max_digits=12)),
                ("closing_cash", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("status", models.CharField(choices=[("OPEN", "Open"), ("CLOSED", "Closed")], default="OPEN", max_length=10)),
                ("notes", models.TextField(blank=True)),
                ("department", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="salon_shifts", to="inventory.department")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="salon_shifts", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-opened_at", "-pk"]},
        ),
        migrations.AddConstraint(
            model_name="salonshift",
            constraint=models.UniqueConstraint(condition=models.Q(("status", "OPEN")), fields=("department",), name="uniq_open_salon_shift_department"),
        ),
        migrations.AddConstraint(
            model_name="salonshift",
            constraint=models.CheckConstraint(condition=models.Q(("opening_cash__gte", 0)), name="salon_shift_opening_cash_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="salonshift",
            constraint=models.CheckConstraint(condition=models.Q(("closing_cash__isnull", True), ("closing_cash__gte", 0), _connector="OR"), name="salon_shift_closing_cash_nonnegative"),
        ),
    ]
