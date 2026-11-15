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
        exclude_reservation_id=reservation.pk,
    )

    # Honor an explicit room assignment; do not silently move the occupant.
    if reservation.room_id:
        assigned_room = available_rooms.filter(pk=reservation.room_id).first()
        if not assigned_room:
            raise ValidationError(
                f"Assigned room {reservation.room.room_number} is no longer available."
            )
        return assigned_room

    available_room = available_rooms.first()

    if not available_room:
        raise ValidationError("No room available.")

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

    # Keep the group allocation synchronized with the individual check-in.
    allocation = getattr(reservation, "group_allocation", None)
    if allocation is not None:
        allocation.room = room
        allocation.folio = folio
        allocation.status = "CHECKED_IN"
        allocation.save(update_fields=["room", "folio", "status"])

    return room, folio