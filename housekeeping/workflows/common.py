from django.core.exceptions import (
    ValidationError,
    PermissionDenied,
)

from accounts.models import User
from accounts.services.access import can_manage_department
from housekeeping.models import CleaningAssignment


def validate_room_requires_cleaning(room):
    """
    Ensure the room actually requires cleaning.
    """

    if room.status not in [
        "VACANT_DIRTY",
        "OCCUPIED_DIRTY",
    ]:
        raise ValidationError(
            "Room does not need cleaning."
        )


def validate_assigner(
    user,
    department,
):
    """
    Ensure the user is allowed to manage the
    specified department.
    """

    if not can_manage_department(
        user,
        department,
    ):
        raise PermissionDenied(
            "You do not have permission to assign "
            "work for this department."
        )


def get_housekeeper(
    department,
    user_id,
):
    """
    Return a valid active housekeeper
    belonging to the specified department.
    """

    try:

        return User.objects.get(
            id=user_id,
            role="HOUSEKEEPING",
            department=department,
            is_active=True,
        )

    except User.DoesNotExist:

        raise ValidationError(
            "Selected housekeeper is not valid for this department."
        )


def get_active_assignment(
    room,
    status=None,
):
    """
    Return the current cleaning assignment for a room.

    If a status is supplied, only an assignment in that
    workflow state is returned.
    """

    assignments = CleaningAssignment.objects.filter(
        room_id=room.id,
    )

    if status:
        assignments = assignments.filter(
            status=status,
        )
    else:
        assignments = assignments.filter(
            status__in=[
                "ASSIGNED",
                "IN_PROGRESS",
                "INSPECTION",
            ],
        )

    return assignments.order_by(
        "-id",
    ).first()