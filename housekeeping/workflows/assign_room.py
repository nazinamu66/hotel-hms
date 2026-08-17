from django.db import transaction
from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from accounts.models import User
from housekeeping.models import (
    CleaningAssignment,
)


def validate_assigner(user):

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

    return User.objects.get(
        id=user_id,
        role="HOUSEKEEPING",
        department=department,
        is_active=True,
    )


def validate_room_assignment(room):

    exists = CleaningAssignment.objects.filter(
        room=room,
        status__in=[
            "ASSIGNED",
            "IN_PROGRESS",
        ],
    ).exists()

    if exists:
        raise ValidationError(
            "Room already has an active cleaning assignment."
        )


def create_assignment(
    room,
    housekeeper,
    assigned_by,
):

    return CleaningAssignment.objects.create(
        room=room,
        assigned_to=housekeeper,
        assigned_by=assigned_by,
    )


@transaction.atomic
def assign_room(
    room,
    assigned_by,
    housekeeper_id,
):

    validate_assigner(
        assigned_by,
    )

    housekeeper = get_housekeeper(
        assigned_by.department,
        housekeeper_id,
    )

    validate_room_assignment(
        room,
    )

    assignment = create_assignment(
        room,
        housekeeper,
        assigned_by,
    )

    return assignment