from django.db import transaction
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils import timezone
from accounts.services.access import can_manage_department
from inventory.models import Department
from housekeeping.workflows.common import (
    get_active_assignment,
)


def get_assignment(room):

    assignment = get_active_assignment(
        room,
        status="INSPECTION",
    )

    if not assignment:
        raise ValidationError(
            "No active cleaning assignment found."
        )

    return assignment


def validate_inspector(
    user,
    department,
):

    if not can_manage_department(
        user,
        department,
    ):
        raise PermissionDenied(
            "You do not have permission to inspect "
            "Housekeeping work."
        )


def validate_assignment(assignment):

    if assignment.status != "INSPECTION":
        raise ValidationError(
            "Cleaning assignment is not awaiting inspection."
        )


def approve_room(assignment):

    room = assignment.room

    if room.status == "VACANT_DIRTY":
        room.status = "AVAILABLE"

    elif room.status == "OCCUPIED_DIRTY":
        room.status = "OCCUPIED"

    else:
        raise ValidationError(
            "Room is not in a valid dirty state for inspection."
        )

    room.save(
        update_fields=[
            "status",
        ]
    )


def record_inspection(
    assignment,
    user,
):

    assignment.inspected_at = timezone.now()
    assignment.inspected_by = user
    assignment.status = "DONE"

    assignment.save(
        update_fields=[
            "inspected_at",
            "inspected_by",
            "status",
        ]
    )


@transaction.atomic
def approve_cleaning(
    room,
    user,
):

    department = (
        Department.objects
        .filter(
            hotel=room.hotel,
            department_type="HOUSEKEEPING",
            is_active=True,
        )
        .first()
    )

    if not department:
        raise ValidationError(
            "This hotel does not have an active "
            "Housekeeping department."
        )

    validate_inspector(
        user,
        department,
    )

    assignment = get_assignment(
        room,
    )

    validate_assignment(
        assignment,
    )

    approve_room(
        assignment,
    )

    record_inspection(
        assignment,
        user,
    )

    return assignment