# Generated for the Linen custody count adjustment audit model.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("linen", "0020_alter_linentransaction_event_type_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LinenCustodyAdjustment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("previous_clean_quantity", models.PositiveIntegerField()),
                ("previous_dirty_quantity", models.PositiveIntegerField()),
                ("counted_clean_quantity", models.PositiveIntegerField()),
                ("counted_dirty_quantity", models.PositiveIntegerField()),
                ("reason", models.TextField()),
                ("reference", models.CharField(max_length=80, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("adjusted_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="linen_custody_adjustments_made", to=settings.AUTH_USER_MODEL)),
                ("custody", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="adjustments", to="linen.linencustody")),
                ("linen_item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="custody_adjustments", to="linen.linenitem")),
                ("staff_member", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="linen_custody_adjustments", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
