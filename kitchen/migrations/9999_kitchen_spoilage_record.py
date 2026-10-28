# Generated for the Kitchen spoilage log.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("kitchen", "0001_initial"),
        ("inventory", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="KitchenSpoilageRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=12)),
                ("reason_code", models.CharField(choices=[("EXPIRED", "Expired"), ("SPOILED", "Spoiled / contaminated"), ("DAMAGED", "Damaged"), ("OVERPRODUCTION", "Overproduction"), ("PREPARATION_ERROR", "Preparation error"), ("OTHER", "Other")], max_length=24)),
                ("notes", models.TextField(blank=True)),
                ("recorded_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("cost_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("review_status", models.CharField(choices=[("PENDING", "Pending review"), ("REVIEWED", "Reviewed")], default="PENDING", max_length=12)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("review_note", models.TextField(blank=True)),
                ("department", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="kitchen_spoilage_records", to="inventory.department")),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="kitchen_spoilage_records", to="inventory.product")),
                ("recorded_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_kitchen_spoilage", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_kitchen_spoilage", to=settings.AUTH_USER_MODEL)),
                ("stock_movement", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="spoilage_record", to="inventory.stockmovement")),
            ],
            options={"ordering": ["-recorded_at", "-id"]},
        ),
    ]
