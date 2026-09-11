from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from housekeeping.workflows.common import (
    get_active_assignment,
)

from linen.models import (
    CleaningLinenCheck,
    LinenCustody,
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


def reconcile_linen_custody(
    assignment,
    user,
):
    """
    Commit the physical linen movement resulting from
    the completed cleaning checklist.

    For every linen item:

        clean custody
            decreases by clean issued

        dirty custody
            increases by dirty collected

        room linen state
            becomes final quantity

    Missing linen is NOT added to HK custody.

    The operation is atomic. If any linen item cannot
    be reconciled, nothing is committed.
    """

    checks = (
        CleaningLinenCheck.objects
        .select_related(
            "linen_item",
            "linen_item__product",
        )
        .filter(
            assignment=assignment,
        )
        .order_by(
            "linen_item__product__name",
        )
    )

    if not checks.exists():

        raise ValidationError(
            "No linen checklist records were found."
        )

    states = {
        state.linen_item_id: state
        for state in (
            RoomLinenState.objects
            .select_for_update()
            .filter(
                room=assignment.room,
            )
        )
    }

    for check in checks:

        state = states.get(
            check.linen_item_id
        )

        if not state:

            raise ValidationError(
                f"{check.linen_item.product.name}: "
                "Linen state no longer exists for this room."
            )

        clean_issued = (
            check.clean_issued_quantity
        )

        dirty_collected = (
            check.dirty_collected_quantity
        )

        final_quantity = (
            check.final_quantity
        )

        # ----------------------------------------------------
        # Get and lock HK custody
        # ----------------------------------------------------

        custody = (
            LinenCustody.objects
            .select_for_update()
            .filter(
                user=user,
                linen_item=check.linen_item,
            )
            .first()
        )

        if clean_issued > 0 and not custody:

            raise ValidationError(
                f"{check.linen_item.product.name}: "
                "You do not have any clean linen in your custody."
            )

        if custody:

            if clean_issued > custody.clean_quantity:

                raise ValidationError(
                    f"{check.linen_item.product.name}: "
                    f"You have only "
                    f"{custody.clean_quantity} clean linen "
                    "in your custody, but "
                    f"{clean_issued} are required."
                )

        # ----------------------------------------------------
        # Update HK custody
        # ----------------------------------------------------

        if clean_issued > 0 or dirty_collected > 0:

            if not custody:

                custody = (
                    LinenCustody.objects.create(
                        user=user,
                        linen_item=check.linen_item,
                        clean_quantity=0,
                        dirty_quantity=0,
                    )
                )

            custody.clean_quantity -= clean_issued
            custody.dirty_quantity += dirty_collected

            custody.save(
                update_fields=[
                    "clean_quantity",
                    "dirty_quantity",
                    "updated_at",
                ],
            )

        # ----------------------------------------------------
        # Commit room linen state
        # ----------------------------------------------------

        state.quantity = final_quantity

        state.save(
            update_fields=[
                "quantity",
                "updated_at",
            ],
        )


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