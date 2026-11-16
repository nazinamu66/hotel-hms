# Generated for auditable complimentary breakfast redemptions.
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0018_complimentary_meal_entry_type"),
        ("billing", "0036_complimentary_room_night"),
        ("restaurant", "0024_restaurantspoilagerecord"),
        ("rooms", "0012_alter_amenity_hotel"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="posorder",
            name="is_complimentary_breakfast",
            field=models.BooleanField(
                default=False,
                help_text="Order redeems a complimentary breakfast entitlement and is not guest revenue.",
            ),
        ),
        migrations.CreateModel(
            name="ComplimentaryBreakfastRedemption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("room_night_date", models.DateField()),
                ("breakfast_date", models.DateField()),
                ("redeemed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("folio", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="breakfast_redemptions", to="billing.folio")),
                ("room", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="breakfast_redemptions", to="rooms.room")),
                ("order", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="breakfast_redemption", to="restaurant.posorder")),
                ("redeemed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="complimentary_breakfast_redemptions", to=settings.AUTH_USER_MODEL)),
                ("source_charge", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="breakfast_redemptions", to="billing.charge")),
            ],
            options={"ordering": ["-redeemed_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="complimentarybreakfastredemption",
            constraint=models.UniqueConstraint(fields=("room", "breakfast_date"), name="unique_room_breakfast_redemption_per_day"),
        ),
        migrations.AddConstraint(
            model_name="complimentarybreakfastredemption",
            constraint=models.UniqueConstraint(fields=("source_charge",), name="unique_breakfast_redemption_per_room_night"),
        ),
    ]
