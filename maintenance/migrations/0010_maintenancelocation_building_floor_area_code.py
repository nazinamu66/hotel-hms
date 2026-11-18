# Add physical placement metadata to maintenance locations.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("maintenance", "0009_equipment_and_locations"),
    ]

    operations = [
        migrations.AddField(
            model_name="maintenancelocation",
            name="building",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="maintenancelocation",
            name="floor",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="maintenancelocation",
            name="area_code",
            field=models.CharField(blank=True, max_length=40),
        ),
    ]
