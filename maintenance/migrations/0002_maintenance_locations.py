from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0001_initial"),
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="MaintenanceLocation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150)),
                ("location_type", models.CharField(
                    choices=[
                        ("RECEPTION", "Reception"), ("LOBBY", "Lobby"),
                        ("KITCHEN", "Kitchen"), ("RESTAURANT", "Restaurant / Dining Area"),
                        ("LAUNDRY", "Laundry"), ("CORRIDOR", "Hallway / Corridor"),
                        ("STAIRWELL", "Stairwell"), ("GROUNDS", "Grounds"),
                        ("PARKING", "Parking"), ("GATE", "Gate / Entrance"),
                        ("OFFICE", "Office"), ("STORE", "Store / Warehouse"),
                        ("GYM", "Gym"), ("PUBLIC_AREA", "Other Public Area"),
                        ("PLANT_ROOM", "Plant / Utility Room"), ("OTHER", "Other"),
                    ],
                    default="OTHER",
                    max_length=30,
                )),
                ("description", models.TextField(blank=True)),
                ("status", models.CharField(
                    choices=[("ACTIVE", "Active"), ("INACTIVE", "Inactive"), ("UNDER_MAINTENANCE", "Under Maintenance")],
                    default="ACTIVE",
                    max_length=25,
                )),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("hotel", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="maintenance_locations",
                    to="inventory.hotel",
                )),
                ("responsible_departments", models.ManyToManyField(
                    blank=True,
                    related_name="maintenance_locations",
                    to="inventory.department",
                )),
            ],
            options={
                "ordering": ["hotel__name", "name"],
                "constraints": [
                    models.UniqueConstraint(fields=("hotel", "name"), name="unique_maintenance_location_per_hotel"),
                ],
            },
        ),
        migrations.AlterField(
            model_name="maintenanceticket",
            name="room",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="maintenance_tickets",
                to="rooms.room",
            ),
        ),
        migrations.AddField(
            model_name="maintenanceticket",
            name="location",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="maintenance_tickets",
                to="maintenance.maintenancelocation",
            ),
        ),
        migrations.AddField(
            model_name="maintenanceasset",
            name="location",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="assets",
                to="maintenance.maintenancelocation",
            ),
        ),
        migrations.AddField(
            model_name="maintenanceticket",
            name="asset",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="maintenance_tickets",
                to="maintenance.maintenanceasset",
            ),
        ),
    ]
