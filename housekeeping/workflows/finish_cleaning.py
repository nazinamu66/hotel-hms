from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from housekeeping.workflows.common import (
    get_active_assignment,
)

from linen.models import (
    CleaningLinenCheck,
    LinenCustody,
    LinenTransaction,
    RoomLinenState,
)


def get_assignment(room):

    assignment = get_active_assignment(
        room,
        status="IN_PROGRESS",
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

    if assignment.status != "IN_PROGRESS":
        raise ValidationError(
            "Cleaning has not been started."
        )

    if assignment.assigned_to != user:
        raise ValidationError(
            "This cleaning assignment belongs to another housekeeper."
        )


def validate_linen_checklist(
    assignment,
):
    """
    Ensure the linen checklist has been completed
    for every linen item currently tracked for the room.
    """

    states = (
        RoomLinenState.objects
        .filter(
            room=assignment.room,
        )
        .values_list(
            "linen_item_id",
            flat=True,
        )
    )

    checks = (
        CleaningLinenCheck.objects
        .filter(
            assignment=assignment,
        )
        .values_list(
            "linen_item_id",
            flat=True,
        )
    )

    state_ids = set(states)
    check_ids = set(checks)

    if state_ids - check_ids:

        raise ValidationError(
            "The linen checklist has not been completed "
            "for every linen item in this room."
        )

    if not state_ids:

        raise ValidationError(
            "No linen has been configured for this room."
        )


def reconcile_linen_custody(assignment, user):
    """Snapshot and reconcile one checklist submission exactly once."""
    from django.db.models import Max
    from linen.models import CleaningLinenSubmission, LinenItem

    assignment = type(assignment).objects.select_for_update().get(pk=assignment.pk)
    if assignment.status != "IN_PROGRESS":
        raise ValidationError("This cleaning assignment is not open for submission.")
    checks = list(
        CleaningLinenCheck.objects.select_related("linen_item", "linen_item__product")
        .filter(assignment=assignment).order_by("linen_item__product__name")
    )
    if not checks:
        raise ValidationError("No linen checklist records were found.")

    latest = CleaningLinenSubmission.objects.filter(assignment=assignment).order_by("-sequence").first()
    inspection_count = assignment.inspections.count()
    if latest and inspection_count <= latest.inspection_count:
        raise ValidationError("This checklist has already been submitted for inspection.")

    previous_sequence = (
        CleaningLinenSubmission.objects.filter(assignment=assignment)
        .aggregate(value=Max("sequence"))["value"] or 0
    )
    snapshot = [
        {
            "linen_item_id": check.linen_item_id,
            "linen_item_name": check.linen_item.product.name,
            "expected_quantity": check.expected_quantity,
            "found_quantity": check.found_quantity,
            "dirty_collected_quantity": check.dirty_collected_quantity,
            "clean_issued_quantity": check.clean_issued_quantity,
            "final_quantity": check.final_quantity,
            "note": check.note,
        }
        for check in checks
    ]
    submission = CleaningLinenSubmission.objects.create(
        assignment=assignment, sequence=previous_sequence + 1,
        submitted_by=user, snapshot=snapshot, inspection_count=inspection_count,
    )
    states = {
        state.linen_item_id: state
        for state in RoomLinenState.objects.select_for_update().filter(room=assignment.room)
    }
    for entry in snapshot:
        item_id = entry["linen_item_id"]
        state = states.get(item_id)
        if state is None:
            raise ValidationError(
                f'{entry["linen_item_name"]}: Linen state no longer exists for this room.'
            )
        linen_item = LinenItem.objects.get(pk=item_id)
        clean_issued = entry["clean_issued_quantity"]
        dirty_collected = entry["dirty_collected_quantity"]
        custody = (
            LinenCustody.objects.select_for_update()
            .filter(user=user, linen_item=linen_item).first()
        )
        if clean_issued and not custody:
            raise ValidationError(
                f'{entry["linen_item_name"]}: You do not have any clean linen in your custody.'
            )
        if custody and clean_issued > custody.clean_quantity:
            raise ValidationError(
                f'{entry["linen_item_name"]}: You have only {custody.clean_quantity} clean linen '
                f'in your custody, but {clean_issued} are required.'
            )
        if clean_issued or dirty_collected:
            if not custody:
                custody = LinenCustody.objects.create(
                    user=user, linen_item=linen_item, clean_quantity=0, dirty_quantity=0,
                )
            custody.clean_quantity -= clean_issued
            custody.dirty_quantity += dirty_collected
            custody.save(update_fields=["clean_quantity", "dirty_quantity", "updated_at"])

        reference = f"HK-CLEAN-{assignment.pk}-SUB-{submission.sequence}"
        room_label = assignment.room.room_number
        if clean_issued:
            LinenTransaction.objects.create(
                linen_item=linen_item, quantity=clean_issued, condition="CLEAN",
                event_type="ROOM_REPLENISHED", performed_by=user, from_user=user,
                to_room=assignment.room, cleaning_assignment=assignment, reference=reference,
                note=f"Clean linen placed in Room {room_label} during cleaning submission #{submission.sequence}.",
            )
        if dirty_collected:
            LinenTransaction.objects.create(
                linen_item=linen_item, quantity=dirty_collected, condition="DIRTY",
                event_type="DIRTY_COLLECTED", performed_by=user, from_room=assignment.room,
                to_user=user, cleaning_assignment=assignment, reference=reference,
                note=f"Dirty linen collected from Room {room_label} during cleaning submission #{submission.sequence}.",
            )
        state.quantity = entry["final_quantity"]
        state.save(update_fields=["quantity", "updated_at"])

    submission.reconciled_at = timezone.now()
    submission.save(update_fields=["reconciled_at"])
    return submission


def mark_cleaning_finished(
    assignment,
):

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

    validate_linen_checklist(
        assignment,
    )

    reconcile_linen_custody(
        assignment,
        user,
    )

    mark_cleaning_finished(
        assignment,
    )

    return assignment