from django.db import transaction
from django.core.exceptions import ValidationError

from rooms.models import (
    Amenity,
    RoomAmenity,
)


def get_amenity(
    room,
    amenity_id,
):
    try:
        return Amenity.objects.get(
            id=amenity_id,
            hotel=room.hotel,
            is_active=True,
        )

    except Amenity.DoesNotExist:
        raise ValidationError(
            "Amenity does not belong to this hotel or is not active."
        )


def validate_room(
    room,
):
    if not room:
        raise ValidationError(
            "Room is required."
        )


def validate_existing_assignment(
    room,
    amenity,
):
    exists = RoomAmenity.objects.filter(
        room=room,
        amenity=amenity,
    ).exists()

    if exists:
        raise ValidationError(
            "Amenity is already assigned to this room."
        )


def validate_quantity(
    quantity,
):
    if quantity < 1:
        raise ValidationError(
            "Quantity must be at least 1."
        )


def create_room_amenity(
    room,
    amenity,
    quantity,
):
    return RoomAmenity.objects.create(
        room=room,
        amenity=amenity,
        quantity=quantity,
    )


@transaction.atomic
def assign_amenity(
    room,
    amenity_id,
    quantity=1,
):

    validate_room(room)

    validate_quantity(
        quantity,
    )

    amenity = get_amenity(
        room,
        amenity_id,
    )

    validate_existing_assignment(
        room,
        amenity,
    )

    return create_room_amenity(
        room,
        amenity,
        quantity,
    )
