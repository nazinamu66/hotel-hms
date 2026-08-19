from django.db import transaction
from django.core.exceptions import ValidationError
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


def validate_assignment(
    assignment,
    user,
):

    if assignment.status != "IN_PROGRESS":
        raise ValidationError(
            "Cleaning has not been started."
        )

    if assignment.assigned_to != user:
        raise ValidationError(
            "This cleaning assignment belongs to another housekeeper."
        )


def mark_cleaning_finished(assignment):

    assignment.status = "INSPECTION"
    assignment.completed_at = timezone.now()

    assignment.save(
        update_fields=[
            "status",
            "completed_at",
        ]
    )


@transaction.atomic
def finish_cleaning(
    room,
    user,
):

    assignment = get_assignment(room)

    validate_assignment(
        assignment,
        user,
    )

    mark_cleaning_finished(
        assignment,
    )

    return assignment