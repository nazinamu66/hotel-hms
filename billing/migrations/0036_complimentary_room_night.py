# Generated for auditable complimentary room-night approvals.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0035_room_discount_audit_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ComplimentaryRoomNight",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("room_night_date", models.DateField()),
                ("waived_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("rate_source", models.CharField(blank=True, max_length=12)),
                ("reason", models.TextField()),
                ("status", models.CharField(choices=[("PENDING", "Pending approval"), ("APPROVED", "Approved"), ("REJECTED", "Rejected"), ("CONSUMED", "Consumed")], default="PENDING", max_length=12)),
                ("requested_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("rejection_reason", models.TextField(blank=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="complimentary_nights_approved", to=settings.AUTH_USER_MODEL)),
                ("folio", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="complimentary_room_nights", to="billing.folio")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="complimentary_nights_requested", to=settings.AUTH_USER_MODEL)),
                ("reservation", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="complimentary_room_nights", to="billing.reservation")),
            ],
            options={"ordering": ["room_night_date", "id"]},
        ),
        migrations.AddConstraint(
            model_name="complimentaryroomnight",
            constraint=models.UniqueConstraint(fields=("reservation", "room_night_date"), name="unique_complimentary_reservation_night"),
        ),
    ]
