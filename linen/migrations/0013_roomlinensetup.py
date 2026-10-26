# Generated for persistent room-specific linen setup state.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("linen", "0012_alter_linenissue_source_and_more"),
        ("rooms", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="RoomLinenSetup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_configured", models.BooleanField(default=False)),
                ("configured_at", models.DateTimeField(auto_now=True)),
                ("room", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="linen_setup", to="rooms.room")),
            ],
        ),
    ]
