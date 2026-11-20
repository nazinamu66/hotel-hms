from django.core.exceptions import ValidationError
from django.db import transaction

from maintenance.models import MaintenanceSchedule, MaintenanceScheduleCycle, MaintenanceTicket


@transaction.atomic
def create_preventive_work_order(
    schedule,
    user,
):
    """
    Create a MaintenanceTicket from a preventive
    maintenance schedule.

    A schedule may have only one active preventive
    work order at a time. Assets must be assigned to
    either a room or a non-room maintenance location.
    """

    if not schedule.is_active:
        raise ValidationError(
            "This maintenance schedule is inactive."
        )

    asset = schedule.asset

    if not asset.is_active:
        raise ValidationError(
            "The asset associated with this schedule is inactive."
        )

    # A ticket must point to exactly one operational location.
    # Unassigned assets need to be assigned before a work order
    # can be raised.
    if not asset.room_id and not asset.location_id:
        raise ValidationError(
            "This asset is not assigned to a room or maintenance location. "
            "Assign it before generating a preventive work order."
        )

    if asset.room_id and asset.location_id:
        raise ValidationError(
            "This asset has conflicting room and maintenance location assignments. "
            "Correct the asset location before generating a work order."
        )

    if not user.hotel_id:
        raise ValidationError(
            "User is not assigned to a hotel."
        )

    if user.hotel_id != asset.hotel_id:
        raise ValidationError(
            "You cannot create a work order "
            "for another hotel."
        )

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
        cycle, _ = MaintenanceScheduleCycle.objects.get_or_create(
            schedule=schedule,
            due_date=schedule.next_due_date,
        )
        if cycle.work_order_id is None:
            cycle.work_order = existing
            cycle.save(update_fields=["work_order"])
        return existing

    cycle, _ = MaintenanceScheduleCycle.objects.get_or_create(
        schedule=schedule,
        due_date=schedule.next_due_date,
    )

    ticket = MaintenanceTicket.objects.create(
        room=asset.room if asset.room_id else None,
        location=asset.location if asset.location_id else None,
        asset=asset,
        schedule=schedule,
        description=(
            f"Preventive maintenance: "
            f"{schedule.name} — "
            f"{asset.name}"
        ),
        priority="MEDIUM",
        reported_by=user,
    )

    cycle.work_order = ticket
    cycle.save(update_fields=["work_order"])

    return ticket
