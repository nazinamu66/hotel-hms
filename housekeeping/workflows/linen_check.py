from django.core.exceptions import ValidationError
from django.db import transaction

from housekeeping.models import CleaningAssignment
from linen.models import (
    CleaningLinenCheck,
    RoomLinenState,
)


def get_assignment(assignment_id):
    try:
        return (
            CleaningAssignment.objects
            .select_related(
                "room",
                "room__category",
                "assigned_to",
            )
            .get(pk=assignment_id)
        )

    except CleaningAssignment.DoesNotExist:
        raise ValidationError(
            "Cleaning assignment does not exist."
        )


def validate_assignment(
    assignment,
    user,
):
    if assignment.status != "IN_PROGRESS":
        raise ValidationError(
            "Linen checklist is only available while cleaning is in progress."
        )

    if assignment.assigned_to_id != user.id:
        raise ValidationError(
            "This cleaning assignment belongs to another housekeeper."
        )


@transaction.atomic
def get_or_create_linen_checks(
    assignment,
    user,
):
    """
    Open the linen checklist for an active cleaning assignment.

    The expected quantity is captured from the room's
    current linen state at the beginning of this cycle.

    Existing checks are never reset.
    """

    validate_assignment(
        assignment,
        user,
    )

    states = (
        RoomLinenState.objects
        .filter(
            room=assignment.room,
        )
        .select_related(
            "linen_item",
            "linen_item__product",
        )
        .order_by(
            "linen_item__product__name",
        )
    )

    if not states.exists():
        raise ValidationError(
            "No linen has been configured for this room."
        )

    checks = []

    for state in states:

        check, created = (
            CleaningLinenCheck.objects.get_or_create(
                assignment=assignment,
                linen_item=state.linen_item,
                defaults={
                    "expected_quantity": state.quantity,
                    "found_quantity": state.quantity,
                    "dirty_collected_quantity": state.quantity,
                    "clean_issued_quantity": state.quantity,
                    "final_quantity": state.quantity,
                },
            )
        )

        checks.append(check)

    return checks


@transaction.atomic
def save_linen_checks(
    assignment,
    user,
    checks,
):
    """
    Save the linen checklist.

    `checks` is keyed by linen_item_id.

    Each value contains:

        found_quantity
        dirty_collected_quantity
        clean_issued_quantity
        final_quantity
        note

    IMPORTANT:
    RoomLinenState is NOT updated here.

    The room's linen state and HK linen custody are only
    committed when cleaning is successfully finished.
    """

    validate_assignment(
        assignment,
        user,
    )

    existing_checks = {
        check.linen_item_id: check
        for check in CleaningLinenCheck.objects.filter(
            assignment=assignment,
        )
    }

    if not existing_checks:

        raise ValidationError(
            "Open the linen checklist before submitting it."
        )

    room_state_ids = set(
        RoomLinenState.objects
        .filter(
            room=assignment.room,
        )
        .values_list(
            "linen_item_id",
            flat=True,
        )
    )

    for item_id, values in checks.items():

        try:

            item_id = int(item_id)

        except (TypeError, ValueError):

            raise ValidationError(
                "Invalid linen item."
            )

        if item_id not in existing_checks:

            raise ValidationError(
                "Invalid linen checklist item."
            )

        if item_id not in room_state_ids:

            raise ValidationError(
                "Linen state no longer exists for this room."
            )

        check = existing_checks[item_id]

        try:

            found_quantity = int(
                values.get(
                    "found_quantity",
                    0,
                )
            )

            dirty_collected_quantity = int(
                values.get(
                    "dirty_collected_quantity",
                    0,
                )
            )

            clean_issued_quantity = int(
                values.get(
                    "clean_issued_quantity",
                    0,
                )
            )

            final_quantity = int(
                values.get(
                    "final_quantity",
                    0,
                )
            )

        except (TypeError, ValueError):

            raise ValidationError(
                "Linen quantities must be whole numbers."
            )

        if found_quantity < 0:

            raise ValidationError(
                "Found quantity cannot be negative."
            )

        if dirty_collected_quantity < 0:

            raise ValidationError(
                "Dirty collected quantity cannot be negative."
            )

        if clean_issued_quantity < 0:

            raise ValidationError(
                "Clean issued quantity cannot be negative."
            )

        if final_quantity < 0:

            raise ValidationError(
                "Final quantity cannot be negative."
            )

        # -----------------------------------------------------
        # Physical reconciliation
        # -----------------------------------------------------

        if dirty_collected_quantity != found_quantity:

            raise ValidationError(
                f"{check.linen_item.product.name}: "
                "Dirty collected quantity must equal "
                "the quantity found in the room."
            )

        if final_quantity != clean_issued_quantity:

            raise ValidationError(
                f"{check.linen_item.product.name}: "
                "Final quantity must equal the clean linen "
                "placed in the room."
            )

        check.found_quantity = found_quantity

        check.dirty_collected_quantity = (
            dirty_collected_quantity
        )

        check.clean_issued_quantity = (
            clean_issued_quantity
        )

        check.final_quantity = final_quantity

        check.note = values.get(
            "note",
            "",
        ).strip()

        check.save(
            update_fields=[
                "found_quantity",
                "dirty_collected_quantity",
                "clean_issued_quantity",
                "final_quantity",
                "note",
            ]
        )

    return list(
        CleaningLinenCheck.objects
        .filter(
            assignment=assignment,
        )
        .select_related(
            "linen_item",
            "linen_item__product",
        )
        .order_by(
            "linen_item__product__name",
        )
    )