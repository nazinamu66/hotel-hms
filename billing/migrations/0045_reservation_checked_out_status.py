from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0044_reservationroomallocation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="reservation",
            name="status",
            field=models.CharField(
                choices=[
                    ("RESERVED", "Reserved"),
                    ("CHECKED_IN", "Checked In"),
                    ("CHECKED_OUT", "Checked Out"),
                    ("CANCELLED", "Cancelled"),
                    ("NO_SHOW", "No Show"),
                ],
                default="RESERVED",
                max_length=20,
            ),
        ),
    ]
