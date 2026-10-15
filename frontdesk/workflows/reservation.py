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
    number_of_rooms,
    check_in,
    check_out,
):
    
    if not check_in or not check_out:
        raise ValidationError(
            "Check-in and check-out dates are required."
        )

    # Convert form date strings into real Python date objects.
    from datetime import datetime

    try:
        if isinstance(check_in, str):
            check_in = datetime.strptime(
                check_in,
                "%Y-%m-%d",
            ).date()

        if isinstance(check_out, str):
            check_out = datetime.strptime(
                check_out,
                "%Y-%m-%d",
            ).date()

    except ValueError:
        raise ValidationError(
            "Invalid check-in or check-out date."
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

    try:
        number_of_rooms = int(number_of_rooms)
    except (TypeError, ValueError):
        raise ValidationError(
            "Number of rooms must be a valid whole number."
        )

    if number_of_rooms < 1:
        raise ValidationError(
            "Number of rooms must be at least 1."
        )

    return number_of_rooms, check_in, check_out


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
    number_of_rooms,
):
    available_rooms = get_available_rooms(
        hotel,
        category,
        check_in,
        check_out,
    )

    available_count = available_rooms.count()

    if available_count < number_of_rooms:
        raise ValidationError(
            f"Only {available_count} room(s) are available "
            f"for the selected dates."
        )


def create_reservation_record(
    hotel,
    guest,
    category,
    room,
    number_of_rooms,
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
        number_of_rooms=number_of_rooms,
        check_out_date=check_out,
        created_by=user,
    )


def create_reservation(
    hotel,
    user,
    guest_id,
    category_id=None,
    room=None,
    number_of_rooms=1,
    check_in=None,
    check_out=None,
):

    number_of_rooms, check_in, check_out = validate_reservation_data(
        guest_id,
        category_id,
        room,
        number_of_rooms,
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
        number_of_rooms,
    )

    reservation = create_reservation_record(
        hotel,
        guest,
        category,
        room,
        number_of_rooms,
        check_in,
        check_out,
        user,
    )

    from billing.services.reservation_deposit import (
        create_reservation_guarantee,
    )

    try:
        create_reservation_guarantee(
            reservation=reservation,
            user=user,
        )

    except ValidationError:
        # Reservation remains valid.
        # It simply has no guarantee yet.
        pass

    return reservation