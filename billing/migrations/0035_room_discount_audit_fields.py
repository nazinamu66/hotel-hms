from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0034_groupreservationrate_and_room_rate_snapshots"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="folio",
            name="reservation",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="folios", to="billing.reservation",
            ),
        ),
        migrations.AddField(
            model_name="folio",
            name="room_discount_percent",
            field=models.DecimalField(
                decimal_places=2, default=Decimal("0.00"), max_digits=4,
                help_text="Approved fixed receptionist discount copied from the reservation or walk-in check-in.",
            ),
        ),
        migrations.AddField(
            model_name="folio",
            name="room_discount_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="folio",
            name="room_discount_applied_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="room_discounts_applied", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="reservation",
            name="room_discount_percent",
            field=models.DecimalField(
                decimal_places=2, default=Decimal("0.00"), max_digits=4,
                help_text="Only 0%, 5%, 10% or 15% may be selected.",
            ),
        ),
        migrations.AddField(
            model_name="reservation",
            name="room_discount_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="reservation",
            name="room_discount_applied_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="reservation_room_discounts_applied", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="charge",
            name="original_room_rate",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="charge",
            name="discount_percentage",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=4),
        ),
    ]
