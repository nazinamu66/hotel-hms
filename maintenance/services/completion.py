from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from billing.models import Folio


@transaction.atomic
def complete_maintenance_ticket(
    ticket,
    *,
    outcome,
    resolution_note,
):
    """
    Complete an in-progress MaintenanceTicket.

    The workflow:

        1. Lock the ticket and room.
        2. Validate the ticket and completion data.
        3. Determine the resulting operational room status.
        4. Mark the ticket RESOLVED.
        5. Advance any preventive maintenance schedule.
        6. Persist the room status.
        7. Return the completed ticket.

    The active room folio is the source of truth for current
    guest occupancy.

    The ticket's room_status_before_maintenance field remains
    the historical room state captured when the ticket was created.
    """

    # =========================================================
    # LOCK TICKET
    # =========================================================

    ticket = (
        ticket.__class__.objects
        .select_for_update()
        .select_related("room")
        .get(pk=ticket.pk)
    )

    # =========================================================
    # LOCK ROOM ONLY FOR ROOM-BASED TICKETS
    # =========================================================

    room = None
    if ticket.room_id:
        room = (
            ticket.room.__class__.objects
            .select_for_update()
            .get(pk=ticket.room_id)
        )

    # =========================================================
    # VALIDATE TICKET
    # =========================================================

    if ticket.status != "IN_PROGRESS":
        raise ValidationError(
            "Only an in-progress maintenance ticket "
            "can be completed."
        )

    if not ticket.assigned_to_id:
        raise ValidationError(
            "A maintenance ticket must be assigned "
            "before completion."
        )

    # =========================================================
    # VALIDATE OUTCOME
    # =========================================================

    valid_outcomes = {
        choice[0]
        for choice in ticket.COMPLETION_CHOICES
    }

    if outcome not in valid_outcomes:
        raise ValidationError(
            "Invalid maintenance completion outcome."
        )

    # =========================================================
    # VALIDATE RESOLUTION NOTE
    # =========================================================

    resolution_note = (
        resolution_note or ""
    ).strip()

    if not resolution_note:
        raise ValidationError(
            "A resolution note is required."
        )

    # =========================================================
    # DETERMINE RESULTING ROOM STATUS (ROOM TICKETS ONLY)
    # =========================================================

    resulting_room_status = None
    if room is not None:
        active_folio = Folio.get_active_room_folio(room)
        has_active_folio = active_folio is not None

        if outcome == "ROOM_READY":
            resulting_room_status = "OCCUPIED" if has_active_folio else "AVAILABLE"
        elif outcome == "HOUSEKEEPING_REQUIRED":
            resulting_room_status = "OCCUPIED_DIRTY" if has_active_folio else "VACANT_DIRTY"
        elif outcome == "NOT_APPLICABLE":
            resulting_room_status = ticket.room_status_before_maintenance or room.status
        else:
            raise ValidationError("Invalid completion outcome for a room ticket.")
    elif outcome != "NOT_APPLICABLE":
        raise ValidationError("Non-room maintenance tickets must use the no-room-status-change outcome.")

    # =========================================================
    # COMPLETE TICKET
    # =========================================================

    ticket.status = "RESOLVED"
    ticket.resolved_at = timezone.now()
    ticket.completion_outcome = outcome
    ticket.resolution_note = resolution_note

    ticket.save(
        update_fields=[
            "status",
            "resolved_at",
            "completion_outcome",
            "resolution_note",
        ]
    )

    # =========================================================
    # ADVANCE PREVENTIVE MAINTENANCE SCHEDULE
    # =========================================================
    #
    # Only tickets generated from a preventive maintenance
    # schedule have schedule_id.
    #
    # The next due date is calculated from the ACTUAL
    # completion date.
    # =========================================================

    if ticket.schedule_id:

        schedule = (
            ticket.schedule.__class__.objects
            .select_for_update()
            .get(pk=ticket.schedule_id)
        )

        if schedule.is_active:

            schedule.next_due_date = (
                timezone.localdate()
                + timedelta(
                    days=schedule.interval_days,
                )
            )

            schedule.save(
                update_fields=[
                    "next_due_date",
                ]
            )

    # =========================================================
    # SAVE ROOM STATUS ONLY WHEN A ROOM IS AFFECTED
    # =========================================================

    if room is not None:
        room.status = resulting_room_status
        room.save(update_fields=["status"])

    # =========================================================
    # RETURN COMPLETED TICKET
    # =========================================================

    return ticket