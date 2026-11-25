from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("personnel", "0006_personnelaction_applied_at_employeeprofile_retired"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EmployeeSeparationClearance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("PENDING", "Pending department / Personnel verification"), ("SUBMITTED", "Submitted for final approval"), ("APPROVED", "Approved"), ("RETURNED", "Returned for further action")], default="PENDING", max_length=12)),
                ("outstanding_items", models.TextField(blank=True)),
                ("no_outstanding_confirmed", models.BooleanField(default=False)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("decision_note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("action", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="separation_clearance", to="personnel.personnelaction")),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="decided_separation_clearances", to=settings.AUTH_USER_MODEL)),
                ("submitted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="submitted_separation_clearances", to=settings.AUTH_USER_MODEL)),
                ("verified_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="verified_separation_clearances", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["status", "-created_at", "-pk"],
                "indexes": [models.Index(fields=["status", "created_at"], name="pers_es_status_created")],
            },
        ),
        migrations.CreateModel(
            name="EmployeeSeparationClearanceEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(max_length=20)),
                ("note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="separation_clearance_events", to=settings.AUTH_USER_MODEL)),
                ("clearance", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="events", to="personnel.employeeseparationclearance")),
            ],
            options={"ordering": ["created_at", "pk"]},
        ),
    ]
