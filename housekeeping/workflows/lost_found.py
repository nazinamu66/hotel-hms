from django.db import transaction
from django.core.exceptions import ValidationError

from housekeeping.models import (
    LostFoundItem,
)


def validate_item(
    room,
    description,
):

    if not description:
        raise ValidationError(
            "Item description is required."
        )


def create_item(
    room,
    description,
    found_by,
):

    return LostFoundItem.objects.create(
        room=room,
        description=description,
        found_by=found_by,
    )


@transaction.atomic
def record_item(
    room,
    description,
    found_by,
):

    validate_item(
        room,
        description,
    )

    item = create_item(
        room,
        description,
        found_by,
    )

    return item