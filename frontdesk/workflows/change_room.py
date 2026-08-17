from django.db import transaction
from django.core.exceptions import ValidationError

from billing.models import Folio
from rooms.models import Room


def get_active_folio(room):

    folio = Folio.get_active_room_folio(room)

    if not folio:
        raise ValidationError(
            "No active stay found."
        )

    return folio


def get_new_room(room, new_room_id):

    if not new_room_id:
        raise ValidationError(
            "Please select a room."
        )

    return Room.objects.get(
        id=new_room_id,
        hotel=room.hotel,
    )


def validate_room_change(
    folio,
    new_room,
):

    if new_room.status != "AVAILABLE":
        raise ValidationError(
            "Selected room is not available."
        )

    if folio.room == new_room:
        raise ValidationError(
            "Guest is already assigned to this room."
        )


def move_folio(
    folio,
    new_room,
):

    old_room = folio.room

    folio.room = new_room

    folio.save(
        update_fields=[
            "room",
        ]
    )

    return old_room


def update_old_room(old_room):

    old_room.status = "VACANT_DIRTY"

    old_room.save(
        update_fields=[
            "status",
        ]
    )


def update_new_room(new_room):

    new_room.refresh_status()


@transaction.atomic
def change_guest_room(
    room,
    new_room_id,
):

    folio = get_active_folio(room)

    new_room = get_new_room(
        room,
        new_room_id,
    )

    validate_room_change(
        folio,
        new_room,
    )

    old_room = move_folio(
        folio,
        new_room,
    )

    update_old_room(old_room)

    update_new_room(new_room)

    return old_room, new_room