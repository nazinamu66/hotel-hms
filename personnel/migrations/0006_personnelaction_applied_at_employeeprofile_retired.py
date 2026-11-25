from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("personnel", "0005_personnelaction_personnelactionevent_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="personnelaction",
            name="applied_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AlterField(
            model_name="employeeprofile",
            name="employment_status",
            field=models.CharField(
                choices=[
                    ("ACTIVE", "Active"),
                    ("ON_LEAVE", "On leave"),
                    ("SUSPENDED", "Suspended"),
                    ("RESIGNED", "Resigned"),
                    ("RETIRED", "Retired"),
                    ("TERMINATED", "Terminated"),
                ],
                max_length=12,
                default="ACTIVE",
            ),
        ),
    ]
