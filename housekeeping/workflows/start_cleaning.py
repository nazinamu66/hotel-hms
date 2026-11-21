from django.db import transaction
from django.core.exceptions import ValidationError
from datetime import timedelta

from housekeeping.models import CleaningAssignment
from housekeeping.workflows.common import (
    get_active_assignment,
)


def get_assignment(room):
    """
    Return the active cleaning assignment for the room.
    """

    assignment = get_active_assignment(
        room,
        status="ASSIGNED",
    )

    if not assignment:
        raise ValidationError(
            "No active cleaning assignment found."
        )

    return assignment


def validate_assignment(
    assignment,
    user,
):
    """
    Ensure the assignment can be started by this
    housekeeper.
    """

    if assignment.status != "ASSIGNED":
        raise ValidationError(
            "Cleaning assignment is not ready to start."
        )

    if assignment.assigned_to != user:
        raise ValidationError(
            "This cleaning assignment is assigned to another housekeeper."
        )


def mark_cleaning_started(assignment):
    """
    Move the assignment into IN_PROGRESS and
    record the actual start time.
    """

    from django.utils import timezone

    assignment.status = "IN_PROGRESS"
    assignment.started_at = timezone.now()
    assignment.cleaning_deadline_at = assignment.started_at + timedelta(minutes=20)
    assignment.warning_sent_at = None
    assignment.overdue_alert_sent_at = None

    assignment.save(
        update_fields=[
            "status",
            "started_at",
            "cleaning_deadline_at",
            "warning_sent_at",
            "overdue_alert_sent_at",
        ]
    )


@transaction.atomic
def start_cleaning(
    room,
    user,
):
    """
    Start the cleaning assignment for a room.
    """

    assignment = get_assignment(room)

    validate_assignment(
        assignment,
        user,
    )

    mark_cleaning_started(
        assignment,
    )

    return assignment