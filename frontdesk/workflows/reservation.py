from django.db import transaction
from django.core.exceptions import ValidationError

from billing.models import (
    Guest,
    Reservation,
)

from rooms.models import RoomCategory

from frontdesk.services import get_available_rooms


def validate_reservation_data(
    guest_id,
    category_id,
    room,
    check_in,
    check_out,
):
    if not check_in or not check_out:
        raise ValidationError(
            "Check-in and check-out dates are required."
        )

    if check_out <= check_in:
        raise ValidationError(
            "Check-out date must be after check-in date."
        )

    if not guest_id:
        raise ValidationError(
            "Guest is required."
        )

    if category_id is None and room is None:
        raise ValidationError(
            "Room category is required."
        )


def get_guest(
    hotel,
    guest_id,
):

    return Guest.objects.get(
        id=guest_id,
        hotel=hotel,
    )


def get_room_category(
    category_id,
):

    return RoomCategory.objects.get(
        id=category_id,
    )


def validate_availability(
    hotel,
    category,
    check_in,
    check_out,
):

    available_rooms = get_available_rooms(
        hotel,
        category,
        check_in,
        check_out,
    )

    if not available_rooms.exists():
        raise ValidationError(
            "No rooms available for selected dates."
        )


def create_reservation_record(
    hotel,
    guest,
    category,
    room,
    check_in,
    check_out,
    user,
):

    return Reservation.objects.create(
        guest=guest,
        hotel=hotel,
        room=room,
        room_category=category,
        check_in_date=check_in,
        check_out_date=check_out,
        created_by=user,
    )


@transaction.atomic
def create_reservation(
    hotel,
    user,
    guest_id,
    category_id=None,
    room=None,
    check_in=None,
    check_out=None,
):

    validate_reservation_data(
        guest_id,
        category_id,
        room,
        check_in,
        check_out,
    )

    guest = get_guest(
        hotel,
        guest_id,
    )

    if room is not None:

        category = room.category

    else:

        category = get_room_category(
            category_id,
        )

    validate_availability(
        hotel,
        category,
        check_in,
        check_out,
    )

    reservation = create_reservation_record(
        hotel,
        guest,
        category,
        room,
        check_in,
        check_out,
        user,
    )

    return reservation