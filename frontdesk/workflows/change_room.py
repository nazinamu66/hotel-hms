from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from rooms.models import Room

from frontdesk.workflows.common import (
    get_active_folio,
)
from billing.models import (
    Folio,
    FolioRoomHistory,
)


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

    if folio.room == new_room:
        raise ValidationError(
            "Guest is already assigned to this room."
        )

    active_folio_exists = Folio.objects.filter(
        room=new_room,
        hotel=folio.hotel,
        folio_type="ROOM",
        is_closed=False,
    ).exclude(
        pk=folio.pk,
    ).exists()

    if active_folio_exists:
        raise ValidationError(
            "Selected room already has an active guest stay."
        )

    if new_room.status != "AVAILABLE":
        raise ValidationError(
            "Selected room is not available."
        )

    rate = getattr(
        new_room.category,
        "rate",
        None,
    )

    if not rate or not rate.is_active:
        raise ValidationError(
            "Selected room does not have an active room rate."
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


def close_old_room_history(
    folio,
):

    history = (
        folio.room_history
        .filter(
            ended_at__isnull=True,
        )
        .order_by("-assigned_at")
        .first()
    )

    if not history:
        raise ValidationError(
            "No active room history was found for this folio."
        )

    history.ended_at = timezone.now()

    history.save(
        update_fields=[
            "ended_at",
        ]
    )


def create_new_room_history(
    folio,
    room,
    user,
):

    FolioRoomHistory.objects.create(
        folio=folio,
        room=room,
        reason="ROOM_CHANGE",
        assigned_by=user,
    )


def update_old_room(
    old_room,
):

    old_room.status = "VACANT_DIRTY"

    old_room.save(
        update_fields=[
            "status",
        ]
    )


def update_new_room(
    new_room,
):

    new_room.refresh_status()


@transaction.atomic
def change_guest_room(
    room,
    new_room_id,
    user,
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

    close_old_room_history(
        folio,
    )

    create_new_room_history(
        folio,
        new_room,
        user,
    )

    update_old_room(
        old_room,
    )

    update_new_room(
        new_room,
    )

    return old_room, new_room