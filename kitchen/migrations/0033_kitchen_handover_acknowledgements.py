from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kitchen", "0032_kitchenticket_started_at"),
    ]

    operations = [
        migrations.AddField(model_name="kitchenshifthandover", name="outgoing_acknowledged", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="kitchenshifthandover", name="outgoing_acknowledged_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="kitchenshifthandover", name="incoming_acknowledged", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="kitchenshifthandover", name="incoming_acknowledged_at", field=models.DateTimeField(blank=True, null=True)),
    ]
