from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ("housekeeping", "0010_cleaninglog_assignment"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="cleaningassignment",
            name="cleaning_deadline_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cleaningassignment",
            name="warning_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cleaningassignment",
            name="overdue_alert_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="CleaningTimeExtension",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("minutes_added", models.PositiveSmallIntegerField()),
                ("reason", models.TextField()),
                ("previous_deadline_at", models.DateTimeField()),
                ("new_deadline_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("assignment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="time_extensions", to="housekeeping.cleaningassignment")),
                ("extended_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="housekeeping_time_extensions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("created_at", "id")},
        ),
    ]
