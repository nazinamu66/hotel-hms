# Generated for Kitchen-to-Restaurant finished food handover.
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kitchen", "0028_kitchenspoilagerecord"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="KitchenRestaurantTransfer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=12)),
                ("status", models.CharField(choices=[("PENDING", "Awaiting Restaurant receipt"), ("RECEIVED", "Received"), ("REJECTED", "Rejected")], default="PENDING", max_length=12)),
                ("note", models.TextField(blank=True)),
                ("requested_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("received_at", models.DateTimeField(blank=True, null=True)),
                ("rejected_at", models.DateTimeField(blank=True, null=True)),
                ("rejection_note", models.TextField(blank=True)),
                ("production_batch", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="restaurant_transfers", to="kitchen.productionbatch")),
                ("received_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="received_kitchen_restaurant_transfers", to=settings.AUTH_USER_MODEL)),
                ("rejected_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="rejected_kitchen_restaurant_transfers", to=settings.AUTH_USER_MODEL)),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="requested_kitchen_restaurant_transfers", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-requested_at", "-pk"]},
        ),
    ]
