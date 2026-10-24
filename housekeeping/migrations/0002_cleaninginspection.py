from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("housekeeping", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CleaningInspection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("result", models.CharField(choices=[("PASS", "Passed"), ("FAIL", "Failed")], max_length=10)),
                ("checklist", models.JSONField(default=dict)),
                ("notes", models.TextField(blank=True)),
                ("inspected_at", models.DateTimeField(auto_now_add=True)),
                ("assignment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="inspections", to="housekeeping.cleaningassignment")),
                ("inspected_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="cleaning_inspections", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-inspected_at", "-id")},
        ),
    ]
