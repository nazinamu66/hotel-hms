# Room-night waivers can target one ordinary room allocation.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0045_reservation_checked_out_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="complimentaryroomnight",
            name="allocation",
            field=models.ForeignKey(
                blank=True,
                help_text="The specific ordinary room allocation covered by this waiver. Null is retained for legacy and corporate reservation-level waivers.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="complimentary_room_nights",
                to="billing.reservationroomallocation",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="complimentaryroomnight",
            name="unique_complimentary_reservation_night",
        ),
        migrations.AddConstraint(
            model_name="complimentaryroomnight",
            constraint=models.UniqueConstraint(
                condition=models.Q(("allocation__isnull", True)),
                fields=("reservation", "room_night_date"),
                name="unique_complimentary_reservation_night",
            ),
        ),
        migrations.AddConstraint(
            model_name="complimentaryroomnight",
            constraint=models.UniqueConstraint(
                condition=models.Q(("allocation__isnull", False)),
                fields=("allocation", "room_night_date"),
                name="unique_complimentary_allocation_night",
            ),
        ),
    ]
