# Generated for linking approved cleaning logs to their assignments.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("housekeeping", "0009_lostfounditem_audit_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="cleaninglog",
            name="assignment",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="cleaning_log",
                to="housekeeping.cleaningassignment",
            ),
        ),
    ]
