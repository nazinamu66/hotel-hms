from django.db import transaction
from django.core.exceptions import ValidationError


def validate_room(room):

    if room.status not in [
        "VACANT_DIRTY",
        "OCCUPIED_DIRTY",
    ]:
        raise ValidationError(
            "Room does not need cleaning."
        )


def mark_room_clean(room):

    previous_status = room.status

    if room.status == "VACANT_DIRTY":

        room.status = "AVAILABLE"

    elif room.status == "OCCUPIED_DIRTY":

        room.status = "OCCUPIED"

    room.save(
        update_fields=[
            "status",
        ]
    )

    return previous_status


from housekeeping.models import CleaningLog


def create_cleaning_log(
    room,
    user,
    previous_status,
):

    return CleaningLog.objects.create(
        room=room,
        cleaned_by=user,
        previous_status=previous_status,
    )


@transaction.atomic
def clean_room(
    room,
    user,
):

    validate_room(room)

    previous_status = mark_room_clean(
        room,
    )

    log = create_cleaning_log(
        room,
        user,
        previous_status,
    )

    return log