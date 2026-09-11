from django.db import transaction
from django.core.exceptions import ValidationError

from inventory.models import Department
from housekeeping.models import CleaningAssignment

from .common import (
    validate_assigner,
    get_housekeeper,
    validate_room_requires_cleaning,
)


def validate_room_assignment(room):

    exists = CleaningAssignment.objects.filter(
        room=room,
        status__in=[
            "ASSIGNED",
            "IN_PROGRESS",
            "INSPECTION",
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

    housekeeping_department = (
        Department.objects
        .filter(
            hotel=room.hotel,
            department_type="HOUSEKEEPING",
            is_active=True,
        )
        .first()
    )

    if not housekeeping_department:
        raise ValidationError(
            "This hotel does not have an active "
            "Housekeeping department."
        )

    validate_assigner(
        assigned_by,
        housekeeping_department,
    )

    validate_room_requires_cleaning(
        room,
    )

    validate_room_assignment(
        room,
    )

    housekeeper = get_housekeeper(
        housekeeping_department,
        housekeeper_id,
    )

    assignment = create_assignment(
        room,
        housekeeper,
        assigned_by,
    )

    return assignment