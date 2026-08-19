from django.core.exceptions import ValidationError
from accounts.models import User

"""
Shared helper functions for Housekeeping workflows.

Only reusable retrieval, validation, permission
checks and utility functions belong here.
"""

from django.core.exceptions import (
    ValidationError,
    PermissionDenied,
)

from accounts.models import User
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


def validate_assigner(user):
    """
    Only department heads, managers and admins
    may assign cleaning work.
    """

    if (
        not user.is_department_head
        and user.role not in [
            "MANAGER",
            "ADMIN",
        ]
    ):
        raise PermissionDenied(
            "Only the department head can assign rooms."
        )


def get_housekeeper(
    department,
    user_id,
):
    """
    Return a valid housekeeper.
    """

    return User.objects.get(
        id=user_id,
        role="HOUSEKEEPING",
        department=department,
        is_active=True,
    )


def get_active_assignment(room):
    """
    Return the active cleaning assignment
    for a room.
    """

    return (
        CleaningAssignment.objects
        .filter(
            room=room,
            status__in=[
                "ASSIGNED",
                "IN_PROGRESS",
            ],
        )
        .first()
    )