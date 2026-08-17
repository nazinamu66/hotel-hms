from django.db import transaction
from django.core.exceptions import ValidationError

from frontdesk.services import get_available_rooms
from frontdesk.workflows.check_in import (
        check_in_guest,
    )


def validate_reservation(reservation):

    if reservation.status != "RESERVED":
        raise ValidationError(
            "Reservation not valid."
        )


def find_available_room(reservation):

    available_rooms = get_available_rooms(
        reservation.hotel,
        reservation.room_category,
        reservation.check_in_date,
        reservation.check_out_date,
    )

    available_room = available_rooms.first()

    if not available_room:
        raise ValidationError(
            "No room available."
        )

    return available_room


def mark_reservation_checked_in(
    reservation,
    room,
):
    reservation.room = room

    reservation.status = "CHECKED_IN"

    reservation.save(
        update_fields=[
            "room",
            "status",
        ]
    )


@transaction.atomic
def check_in_reservation(
    reservation,
    user,
):

    validate_reservation(
        reservation,
    )

    room = find_available_room(
        reservation,
    )

    

    folio = check_in_guest(
        room=room,
        user=user,
        guest=reservation.guest,
        expected_checkout=reservation.check_out_date,
    )

    mark_reservation_checked_in(
        reservation,
        room,
    )

    return room, folio