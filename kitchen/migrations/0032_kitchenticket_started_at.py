from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kitchen", "0031_kitchenrestrictedstocklayer"),
    ]

    operations = [
        migrations.AddField(
            model_name="kitchenticket",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
