import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0040_event_booking_models"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="eventparticipant",
            name="room_allocation",
        ),
        migrations.AddField(
            model_name="eventparticipant",
            name="reservation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="event_participations",
                to="billing.reservation",
            ),
        ),
    ]
