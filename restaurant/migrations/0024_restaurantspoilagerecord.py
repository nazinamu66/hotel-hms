# Generated for approval-gated Restaurant spoilage.
import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0001_initial"),
        ("inventory", "0003_alter_stockmovement_options_and_more"),
        ("restaurant", "0023_alter_posorderitem_cost_at_sale_precision"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RestaurantSpoilageRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=12)),
                ("reason_code", models.CharField(choices=[("OPENED", "Opened / unsealed"), ("DAMAGED", "Damaged / broken"), ("SPOILED", "Spoiled / contaminated"), ("EXPIRED", "Expired"), ("OTHER", "Other")], max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("reported_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("status", models.CharField(choices=[("PENDING", "Pending approval"), ("APPROVED", "Approved"), ("REJECTED", "Rejected")], default="PENDING", max_length=12)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("rejected_at", models.DateTimeField(blank=True, null=True)),
                ("review_note", models.TextField(blank=True)),
                ("cost_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_restaurant_spoilage", to=settings.AUTH_USER_MODEL)),
                ("department", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="restaurant_spoilage_records", to="inventory.department")),
                ("journal_entry", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="restaurant_spoilage_records", to="accounting.journalentry")),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="restaurant_spoilage_records", to="inventory.product")),
                ("rejected_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="rejected_restaurant_spoilage", to=settings.AUTH_USER_MODEL)),
                ("reported_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reported_restaurant_spoilage", to=settings.AUTH_USER_MODEL)),
                ("stock_movement", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="restaurant_spoilage_record", to="inventory.stockmovement")),
            ],
            options={"ordering": ["-reported_at", "-id"]},
        ),
    ]
