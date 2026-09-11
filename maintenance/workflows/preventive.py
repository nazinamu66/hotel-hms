from django.core.exceptions import ValidationError
from django.db import transaction

from maintenance.models import MaintenanceSchedule, MaintenanceTicket


@transaction.atomic
def create_preventive_work_order(
    schedule,
    user,
):
    """
    Create a MaintenanceTicket from a preventive
    maintenance schedule.

    A schedule may have only one active preventive
    work order at a time.
    """

    # ---------------------------------------------------------
    # Schedule validation
    # ---------------------------------------------------------

    if not schedule.is_active:
        raise ValidationError(
            "This maintenance schedule is inactive."
        )

    asset = schedule.asset

    if not asset.is_active:
        raise ValidationError(
            "The asset associated with this schedule is inactive."
        )

    # ---------------------------------------------------------
    # Preventive maintenance requires a room
    # ---------------------------------------------------------

    if not asset.room_id:
        raise ValidationError(
            "This asset is not assigned to a room. "
            "A work order cannot be generated yet."
        )

    # ---------------------------------------------------------
    # User / hotel security
    # ---------------------------------------------------------

    if not user.hotel_id:
        raise ValidationError(
            "User is not assigned to a hotel."
        )

    if user.hotel_id != asset.hotel_id:
        raise ValidationError(
            "You cannot create a work order "
            "for another hotel."
        )

    # ---------------------------------------------------------
    # Prevent duplicate active work orders
    # ---------------------------------------------------------

    existing = (
        MaintenanceTicket.objects
        .filter(
            schedule=schedule,
            status__in=[
                "OPEN",
                "IN_PROGRESS",
            ],
        )
        .first()
    )

    if existing:
        return existing

    # ---------------------------------------------------------
    # Create preventive work order
    # ---------------------------------------------------------

    ticket = MaintenanceTicket.objects.create(
        room=asset.room,
        schedule=schedule,
        description=(
            f"Preventive maintenance: "
            f"{schedule.name} — "
            f"{asset.name}"
        ),
        priority="MEDIUM",
        reported_by=user,
    )

    return ticket