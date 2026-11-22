from django.db import migrations, models
import django.db.models.deletion


def assign_category_hotels(apps, schema_editor):
    RoomCategory = apps.get_model("rooms", "RoomCategory")
    Room = apps.get_model("rooms", "Room")
    Hotel = apps.get_model("inventory", "Hotel")

    for category in RoomCategory.objects.all().iterator():
        hotel_ids = set(
            Room.objects.filter(category_id=category.pk)
            .values_list("hotel_id", flat=True)
            .distinct()
        )
        if len(hotel_ids) > 1:
            raise RuntimeError(
                f"Room category {category.pk} is currently shared by multiple hotels. "
                "Assign separate categories before applying hotel ownership."
            )
        if hotel_ids:
            hotel_id = next(iter(hotel_ids))
        else:
            hotel_ids = list(Hotel.objects.values_list("pk", flat=True)[:2])
            if len(hotel_ids) != 1:
                raise RuntimeError(
                    f"Cannot determine the hotel for unused room category {category.pk}. "
                    "Create or select its owning hotel before migrating."
                )
            hotel_id = hotel_ids[0]
        category.hotel_id = hotel_id
        category.save(update_fields=["hotel"])


class Migration(migrations.Migration):

    dependencies = [
        ("rooms", "0012_alter_amenity_hotel"),
        ("inventory", "0032_alter_hotelfeature_feature"),
    ]

    operations = [
        migrations.AlterField(
            model_name="roomcategory",
            name="name",
            field=models.CharField(max_length=100),
        ),
        migrations.AddField(
            model_name="roomcategory",
            name="hotel",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="room_categories",
                to="inventory.hotel",
            ),
        ),
        migrations.RunPython(assign_category_hotels, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="roomcategory",
            name="hotel",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="room_categories",
                to="inventory.hotel",
            ),
        ),
        migrations.AddConstraint(
            model_name="roomcategory",
            constraint=models.UniqueConstraint(
                fields=("hotel", "name"),
                name="unique_room_category_per_hotel",
            ),
        ),
    ]
