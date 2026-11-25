from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("personnel", "0008_department_shift_definitions"),
    ]

    operations = [
        migrations.AddField(
            model_name="weeklyroster",
            name="rest_days_per_employee",
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text="Number of rest days allocated to each employee for this roster week.",
            ),
        ),
    ]
