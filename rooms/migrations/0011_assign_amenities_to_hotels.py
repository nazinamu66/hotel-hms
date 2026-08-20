from django.db import migrations


def assign_amenities_to_hotels(apps, schema_editor):

    Amenity = apps.get_model("rooms", "Amenity")
    RoomAmenity = apps.get_model("rooms", "RoomAmenity")

    for amenity in Amenity.objects.filter(hotel__isnull=True):

        hotels = (
            RoomAmenity.objects
            .filter(amenity_id=amenity.id)
            .values_list(
                "room__hotel_id",
                flat=True,
            )
            .distinct()
        )

        hotels = list(hotels)

        # No room currently uses this amenity.
        if not hotels:
            continue

        # First hotel keeps the original record.
        first_hotel_id = hotels[0]

        amenity.hotel_id = first_hotel_id
        amenity.save(
            update_fields=["hotel"],
        )

        # If other hotels use this same old global
        # amenity, create a separate amenity record
        # for each hotel and reassign their RoomAmenity rows.
        for hotel_id in hotels[1:]:

            new_amenity = Amenity.objects.create(
                hotel_id=hotel_id,
                name=amenity.name,
                description=amenity.description,
                is_active=amenity.is_active,
            )

            RoomAmenity.objects.filter(
                amenity_id=amenity.id,
                room__hotel_id=hotel_id,
            ).update(
                amenity_id=new_amenity.id,
            )


def reverse_assign_amenities(apps, schema_editor):
    # This migration is data normalization.
    # We intentionally don't attempt to merge hotel-specific
    # amenities back into a global catalogue automatically.
    pass


class Migration(migrations.Migration):

    dependencies = [
        # CHANGE THIS to the migration immediately before
        # the migration that added Amenity.hotel.
    ("rooms", "0010_amenity_hotel_alter_amenity_name_and_more"),

    ]

    operations = [
        migrations.RunPython(
            assign_amenities_to_hotels,
            reverse_assign_amenities,
        ),
    ]