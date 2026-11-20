from django.db import migrations, models
import django.db.models.deletion


def seed_current_schedule_cycles(apps, schema_editor):
    Schedule = apps.get_model("maintenance", "MaintenanceSchedule")
    Cycle = apps.get_model("maintenance", "MaintenanceScheduleCycle")
    Ticket = apps.get_model("maintenance", "MaintenanceTicket")
    for schedule in Schedule.objects.all().iterator():
        cycle, _ = Cycle.objects.get_or_create(
            schedule_id=schedule.pk,
            due_date=schedule.next_due_date,
        )
        active_ticket = Ticket.objects.filter(
            schedule_id=schedule.pk,
            status__in=["OPEN", "IN_PROGRESS"],
        ).order_by("created_at", "pk").first()
        if active_ticket and cycle.work_order_id is None:
            cycle.work_order_id = active_ticket.pk
            cycle.save(update_fields=["work_order"])


class Migration(migrations.Migration):
    dependencies = [
        ("maintenance", "0010_maintenancelocation_building_floor_area_code"),
    ]

    operations = [
        migrations.CreateModel(
            name="MaintenanceScheduleCycle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("due_date", models.DateField()),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("completion_note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("schedule", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cycles", to="maintenance.maintenanceschedule")),
                ("work_order", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="schedule_cycle", to="maintenance.maintenanceticket")),
            ],
            options={"ordering": ["due_date", "id"]},
        ),
        migrations.AddConstraint(
            model_name="maintenanceschedulecycle",
            constraint=models.UniqueConstraint(fields=("schedule", "due_date"), name="unique_maintenance_schedule_cycle_due"),
        ),
        migrations.RunPython(seed_current_schedule_cycles, migrations.RunPython.noop),
    ]
