# Equipment register and non-room maintenance locations.
from django.db import migrations, models
import django.db.models.deletion


def populate_asset_tags(apps, schema_editor):
    MaintenanceAsset = apps.get_model("maintenance", "MaintenanceAsset")
    for asset in MaintenanceAsset.objects.filter(asset_tag="").order_by("pk"):
        asset.asset_tag = f"EQ-{asset.hotel_id}-{asset.pk:06d}"
        asset.save(update_fields=["asset_tag"])


class Migration(migrations.Migration):
    dependencies = [
        ("maintenance", "0008_maintenanceticket_schedule"),
        ("inventory", "0049_supplier_is_active"),
        ("rooms", "0012_alter_amenity_hotel"),
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
                ("hotel", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="maintenance_locations", to="inventory.hotel")),
                ("responsible_departments", models.ManyToManyField(blank=True, related_name="maintenance_locations", to="inventory.department")),
            ],
            options={
                "ordering": ["hotel__name", "name"],
                "constraints": [models.UniqueConstraint(fields=("hotel", "name"), name="unique_maintenance_location_per_hotel")],
            },
        ),
        migrations.AddField(
            model_name="maintenanceasset",
            name="location",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="assets", to="maintenance.maintenancelocation"),
        ),
        migrations.AddField(
            model_name="maintenanceasset",
            name="product",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="equipment_assets", to="inventory.product"),
        ),
        migrations.AddField(
            model_name="maintenanceasset",
            name="purchase_item",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="registered_assets", to="inventory.purchaseitem"),
        ),
        migrations.AddField(
            model_name="maintenanceasset",
            name="asset_tag",
            field=models.CharField(blank=True, default="", max_length=50),
            preserve_default=False,
        ),
        migrations.RunPython(populate_asset_tags, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="maintenanceasset",
            name="asset_tag",
            field=models.CharField(blank=True, max_length=50, unique=True),
        ),
        migrations.AddField(
            model_name="maintenanceasset",
            name="purchase_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="maintenanceasset",
            name="purchase_cost",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="maintenanceasset",
            name="warranty_expiry",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="maintenanceasset",
            name="condition",
            field=models.CharField(choices=[("NEW", "New"), ("GOOD", "Good"), ("FAIR", "Fair"), ("POOR", "Poor"), ("DAMAGED", "Damaged")], default="GOOD", max_length=12),
        ),
        migrations.AddField(
            model_name="maintenanceticket",
            name="asset",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="maintenance_tickets", to="maintenance.maintenanceasset"),
        ),
        migrations.AlterField(
            model_name="maintenanceticket",
            name="room",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="maintenance_tickets", to="rooms.room"),
        ),
        migrations.AddField(
            model_name="maintenanceticket",
            name="location",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="maintenance_tickets", to="maintenance.maintenancelocation"),
        ),
    ]
