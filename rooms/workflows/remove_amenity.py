from django.db import transaction
from django.core.exceptions import ValidationError

from rooms.models import RoomAmenity


def get_room_amenity(
    room,
    room_amenity_id,
):
    try:

        return RoomAmenity.objects.select_related(
            "amenity",
            "room",
        ).get(
            id=room_amenity_id,
            room=room,
        )

    except RoomAmenity.DoesNotExist:

        raise ValidationError(
            "Amenity is not assigned to this room."
        )


@transaction.atomic
def remove_amenity(
    room,
    room_amenity_id,
):

    room_amenity = get_room_amenity(
        room,
        room_amenity_id,
    )

    room_amenity.delete()

    return True