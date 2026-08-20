from django.db import transaction
from django.core.exceptions import ValidationError

from rooms.models import RoomAmenity


VALID_STATUSES = {
    "AVAILABLE",
    "DAMAGED",
    "MISSING",
    "MAINTENANCE",
}


def get_room_amenity(
    room,
    amenity_id,
):

    try:

        return RoomAmenity.objects.select_related(
            "room",
            "amenity",
        ).get(
            id=amenity_id,
            room=room,
        )

    except RoomAmenity.DoesNotExist:

        raise ValidationError(
            "Amenity is not assigned to this room."
        )


def validate_status(status):

    if status not in VALID_STATUSES:

        raise ValidationError(
            "Invalid amenity status."
        )


def update_amenity_status(
    room_amenity,
    status,
    notes="",
):

    validate_status(
        status,
    )

    room_amenity.status = status
    room_amenity.notes = notes

    room_amenity.save(
        update_fields=[
            "status",
            "notes",
        ]
    )

    return room_amenity


@transaction.atomic
def change_amenity_status(
    room,
    amenity_id,
    status,
    notes="",
):

    room_amenity = get_room_amenity(
        room,
        amenity_id,
    )

    return update_amenity_status(
        room_amenity,
        status,
        notes,
    )