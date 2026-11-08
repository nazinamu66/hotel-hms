from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("housekeeping", "0009_lostfounditem_audit_fields"),
        ("linen", "0015_linentransaction_room_movement_events"),
        ("rooms", "0012_alter_amenity_hotel"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="linentransaction",
            name="from_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="linen_transactions_from",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="linentransaction",
            name="to_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="linen_transactions_to",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="linentransaction",
            name="from_room",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="linen_transactions_from",
                to="rooms.room",
            ),
        ),
        migrations.AddField(
            model_name="linentransaction",
            name="to_room",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="linen_transactions_to",
                to="rooms.room",
            ),
        ),
        migrations.AddField(
            model_name="linentransaction",
            name="cleaning_assignment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="linen_transactions",
                to="housekeeping.cleaningassignment",
            ),
        ),
    ]
