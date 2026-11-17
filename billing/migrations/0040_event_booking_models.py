import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0039_company_invoice_ledger"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EventBooking",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference", models.CharField(max_length=40)),
                ("name", models.CharField(max_length=200)),
                ("event_type", models.CharField(blank=True, max_length=80)),
                ("event_date", models.DateField()),
                ("service_time", models.TimeField()),
                ("location", models.CharField(blank=True, max_length=200)),
                ("expected_participants", models.PositiveIntegerField(default=0)),
                ("status", models.CharField(choices=[("DRAFT", "Draft"), ("AGREED", "Agreed"), ("RELEASED", "Released to Operations"), ("COMPLETED", "Completed"), ("CANCELLED", "Cancelled")], db_index=True, default="DRAFT", max_length=16)),
                ("agreement_notes", models.TextField(blank=True)),
                ("released_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="event_bookings", to="billing.company")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="events_created", to=settings.AUTH_USER_MODEL)),
                ("group_reservation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="event_bookings", to="billing.groupreservation")),
                ("hotel", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="event_bookings", to="inventory.hotel")),
                ("released_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="events_released", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["event_date", "service_time", "id"]},
        ),
        migrations.AddConstraint(
            model_name="eventbooking",
            constraint=models.UniqueConstraint(fields=("hotel", "reference"), name="unique_event_reference_per_hotel"),
        ),
        migrations.AddConstraint(
            model_name="eventbooking",
            constraint=models.CheckConstraint(condition=models.Q(("expected_participants__gte", 0)), name="event_participant_count_nonnegative"),
        ),
        migrations.CreateModel(
            name="EventParticipant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("participant_type", models.CharField(choices=[("IN_HOUSE", "In-house guest"), ("EXTERNAL", "External participant")], max_length=12)),
                ("external_name", models.CharField(blank=True, max_length=150)),
                ("external_organization", models.CharField(blank=True, max_length=150)),
                ("note", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="participants", to="billing.eventbooking")),
                ("guest", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="event_participations", to="billing.guest")),
                ("room_allocation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="event_participations", to="billing.groupreservationroom")),
            ],
            options={"ordering": ["participant_type", "id"]},
        ),
        migrations.CreateModel(
            name="EventOrderLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("line_type", models.CharField(choices=[("FOOD", "Food"), ("BEVERAGE", "Beverage"), ("OTHER", "Other")], max_length=10)),
                ("description", models.CharField(max_length=200)),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=12)),
                ("unit_price", models.DecimalField(decimal_places=2, max_digits=14)),
                ("notes", models.CharField(blank=True, max_length=255)),
                ("event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="order_lines", to="billing.eventbooking")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddConstraint(
            model_name="eventorderline",
            constraint=models.CheckConstraint(condition=models.Q(("quantity__gt", 0)), name="event_line_quantity_positive"),
        ),
        migrations.AddConstraint(
            model_name="eventorderline",
            constraint=models.CheckConstraint(condition=models.Q(("unit_price__gte", 0)), name="event_line_unit_price_nonnegative"),
        ),
    ]
