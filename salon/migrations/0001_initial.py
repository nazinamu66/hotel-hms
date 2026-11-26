import decimal
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("accounting", "0022_salon_revenue_account"),
        ("billing", "0047_folio_checkout_authorization_and_balance_waiver"),
        ("inventory", "0058_alter_hotelfeature_feature"),
        ("personnel", "0009_weeklyroster_rest_days_per_employee"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SalonDiscountPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("employee_discount_percent", models.DecimalField(decimal_places=2, default=decimal.Decimal("50.00"), max_digits=5)),
                ("is_active", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("hotel", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="salon_discount_policy", to="inventory.hotel")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="updated_salon_discount_policies", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("employee_discount_percent__gte", decimal.Decimal("0.00")), ("employee_discount_percent__lte", decimal.Decimal("100.00"))), name="salon_employee_discount_percent_range"),
                ],
            },
        ),
        migrations.CreateModel(
            name="SalonServiceCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("description", models.TextField(blank=True)),
                ("display_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("hotel", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="salon_service_categories", to="inventory.hotel")),
            ],
            options={
                "ordering": ["display_order", "name"],
                "constraints": [
                    models.UniqueConstraint(fields=("hotel", "name"), name="uniq_salon_category_hotel_name"),
                ],
            },
        ),
        migrations.CreateModel(
            name="SalonService",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150)),
                ("description", models.TextField(blank=True)),
                ("duration_minutes", models.PositiveSmallIntegerField(default=30)),
                ("price", models.DecimalField(decimal_places=2, max_digits=12)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="services", to="salon.salonservicecategory")),
                ("hotel", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="salon_services", to="inventory.hotel")),
            ],
            options={
                "ordering": ["category__display_order", "category__name", "name"],
                "constraints": [
                    models.UniqueConstraint(fields=("hotel", "name"), name="uniq_salon_service_hotel_name"),
                    models.CheckConstraint(condition=models.Q(("price__gte", decimal.Decimal("0.00"))), name="salon_service_price_nonnegative"),
                ],
            },
        ),
        migrations.CreateModel(
            name="SalonOrder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("customer_type", models.CharField(choices=[("HOTEL_GUEST", "Hotel Guest"), ("WALK_IN", "Walk-in Customer"), ("EMPLOYEE", "CCH Employee")], max_length=12)),
                ("walkin_name", models.CharField(blank=True, max_length=150)),
                ("walkin_phone", models.CharField(blank=True, max_length=30)),
                ("subtotal", models.DecimalField(decimal_places=2, default=decimal.Decimal("0.00"), max_digits=12)),
                ("discount_total", models.DecimalField(decimal_places=2, default=decimal.Decimal("0.00"), max_digits=12)),
                ("total_amount", models.DecimalField(decimal_places=2, default=decimal.Decimal("0.00"), max_digits=12)),
                ("status", models.CharField(choices=[("OPEN", "Open"), ("CHARGED", "Charged to Room"), ("PAID", "Paid"), ("CANCELLED", "Cancelled")], default="OPEN", max_length=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_salon_orders", to=settings.AUTH_USER_MODEL)),
                ("department", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="salon_orders", to="inventory.department")),
                ("employee", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="salon_discount_orders", to="personnel.employeeprofile")),
                ("folio", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="salon_orders", to="billing.folio")),
                ("guest", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="salon_orders", to="billing.guest")),
            ],
            options={
                "ordering": ["-created_at", "-pk"],
                "indexes": [
                    models.Index(fields=["department", "created_at"], name="salon_salonor_departm_8c2c16_idx"),
                    models.Index(fields=["customer_type", "status"], name="salon_salonor_custome_4e8d63_idx"),
                ],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("subtotal__gte", 0), ("discount_total__gte", 0), ("total_amount__gte", 0)), name="salon_order_totals_nonnegative"),
                ],
            },
        ),
        migrations.CreateModel(
            name="SalonOrderItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("service_name_snapshot", models.CharField(max_length=150)),
                ("category_name_snapshot", models.CharField(blank=True, max_length=100)),
                ("quantity", models.PositiveSmallIntegerField(default=1)),
                ("unit_price", models.DecimalField(decimal_places=2, max_digits=12)),
                ("discount_percentage", models.DecimalField(decimal_places=2, default=decimal.Decimal("0.00"), max_digits=5)),
                ("discount_amount", models.DecimalField(decimal_places=2, default=decimal.Decimal("0.00"), max_digits=12)),
                ("line_total", models.DecimalField(decimal_places=2, max_digits=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="salon.salonorder")),
                ("performed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="performed_salon_services", to=settings.AUTH_USER_MODEL)),
                ("service", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="order_items", to="salon.salonservice")),
            ],
            options={
                "ordering": ["pk"],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("quantity__gt", 0)), name="salon_order_item_quantity_positive"),
                    models.CheckConstraint(condition=models.Q(("unit_price__gte", 0)), name="salon_order_item_price_nonnegative"),
                    models.CheckConstraint(condition=models.Q(("discount_percentage__gte", 0), ("discount_percentage__lte", 100)), name="salon_order_item_discount_percent_range"),
                    models.CheckConstraint(condition=models.Q(("discount_amount__gte", 0), ("line_total__gte", 0)), name="salon_order_item_amounts_nonnegative"),
                ],
            },
        ),
    ]
