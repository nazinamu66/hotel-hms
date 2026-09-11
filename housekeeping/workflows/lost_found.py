from django.db import transaction
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils import timezone

from housekeeping.models import LostFoundItem
from accounts.services.access import user_can_access_hotel


def validate_item(
    room,
    description,
):
    if not description or not description.strip():
        raise ValidationError(
            "Item description is required."
        )


def validate_manager(user):
    """
    Only Housekeeping HODs and management may
    change the status of a Lost & Found item.
    """

    if (
        user.is_department_head
        or user.role in {
            "MANAGER",
            "ADMIN",
            "DIRECTOR",
        }
    ):
        return

    raise PermissionDenied(
        "You do not have permission to manage Lost & Found items."
    )


def create_item(
    hotel,
    room,
    description,
    found_by,
):
    return LostFoundItem.objects.create(
        hotel=hotel,
        room=room,
        description=description.strip(),
        found_by=found_by,
    )


@transaction.atomic
def record_item(
    hotel,
    room,
    description,
    found_by,
):
    validate_item(
        room,
        description,
    )

    if room and room.hotel_id != hotel.id:
        raise ValidationError(
            "The selected room does not belong to this hotel."
        )

    return create_item(
        hotel=hotel,
        room=room,
        description=description,
        found_by=found_by,
    )


@transaction.atomic
def claim_item(
    item,
    user,
    claimant,
):
    """
    Mark a found item as claimed.
    """

    validate_manager(user)

    if item.status != "FOUND":
        raise ValidationError(
            "Only items currently marked as Found can be claimed."
        )

    if not claimant or not claimant.strip():
        raise ValidationError(
            "Claimant name is required."
        )

    if not user_can_access_hotel(
        user,
        item.hotel,
    ):
        raise PermissionDenied(
            "You do not have access to this hotel's Lost & Found records."
        )

    item.status = "CLAIMED"
    item.claimed_by = claimant.strip()
    item.claimed_at = timezone.now()

    item.save(
        update_fields=[
            "status",
            "claimed_by",
            "claimed_at",
        ]
    )

    return item


@transaction.atomic
def dispose_item(
    item,
    user,
):
    """
    Mark a found item as disposed.
    """

    validate_manager(user)

    if item.status != "FOUND":
        raise ValidationError(
            "Only items currently marked as Found can be disposed."
        )

    if not user_can_access_hotel(
        user,
        item.hotel,
    ):
        raise PermissionDenied(
            "You do not have access to this hotel's Lost & Found records."
        )

    item.status = "DISPOSED"

    item.save(
        update_fields=[
            "status",
        ]
    )

    return item