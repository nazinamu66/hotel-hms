from django.db import transaction
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils import timezone

from housekeeping.workflows.common import (
    get_active_assignment,
)


def get_assignment(room):

    assignment = get_active_assignment(room)

    if not assignment:
        raise ValidationError(
            "No active cleaning assignment found."
        )

    return assignment


def validate_inspector(user):

    if (
        not user.is_department_head
        and user.role not in [
            "MANAGER",
            "ADMIN",
            "DIRECTOR",
        ]
    ):
        raise PermissionDenied(
            "Only a supervisor can approve room cleaning."
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

    validate_inspector(
        user,
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
