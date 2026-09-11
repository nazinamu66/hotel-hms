from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from laundry.models import LaundryReceipt
from linen.models import LinenTransaction


@transaction.atomic
def create_laundry_receipt(
    *,
    linen_item,
    returned_by,
    declared_clean_quantity=0,
    declared_dirty_quantity=0,
    reference="",
    note="",
):
    """
    Create a pending Laundry receipt for linen returned
    by an individual Housekeeping employee.

    This records the Housekeeping declaration only.
    Laundry has not yet confirmed physical receipt.
    """

    try:
        declared_clean_quantity = int(declared_clean_quantity)
        declared_dirty_quantity = int(declared_dirty_quantity)
    except (TypeError, ValueError):
        raise ValidationError(
            "Linen quantities must be whole numbers."
        )

    if declared_clean_quantity < 0:
        raise ValidationError(
            "Declared clean quantity cannot be negative."
        )

    if declared_dirty_quantity < 0:
        raise ValidationError(
            "Declared dirty quantity cannot be negative."
        )

    if (
        declared_clean_quantity == 0
        and declared_dirty_quantity == 0
    ):
        raise ValidationError(
            "At least one linen quantity must be returned."
        )

    return LaundryReceipt.objects.create(
        linen_item=linen_item,
        returned_by=returned_by,
        declared_clean_quantity=declared_clean_quantity,
        declared_dirty_quantity=declared_dirty_quantity,
        reference=reference,
        note=note,
    )


@transaction.atomic
def confirm_laundry_receipt(
    *,
    receipt,
    received_by,
    received_clean_quantity=0,
    received_dirty_quantity=0,
    note="",
):
    """
    Confirm the physical quantities received by Laundry.

    The original SENT_TO_LAUNDRY transaction already records
    the physical movement from Housekeeping to Laundry.

    RECEIVED_AT_LAUNDRY is therefore an acknowledgement event
    and does not create another physical location movement.
    """

    if receipt.status != "PENDING":
        raise ValidationError(
            "This Laundry receipt is no longer pending."
        )

    try:
        received_clean_quantity = int(received_clean_quantity)
        received_dirty_quantity = int(received_dirty_quantity)
    except (TypeError, ValueError):
        raise ValidationError(
            "Linen quantities must be whole numbers."
        )

    if received_clean_quantity < 0:
        raise ValidationError(
            "Received clean quantity cannot be negative."
        )

    if received_dirty_quantity < 0:
        raise ValidationError(
            "Received dirty quantity cannot be negative."
        )

    if (
        received_clean_quantity == 0
        and received_dirty_quantity == 0
    ):
        raise ValidationError(
            "At least one received linen quantity is required."
        )

    if received_clean_quantity > receipt.declared_clean_quantity:
        raise ValidationError(
            "Received clean quantity cannot exceed the declared quantity."
        )

    if received_dirty_quantity > receipt.declared_dirty_quantity:
        raise ValidationError(
            "Received dirty quantity cannot exceed the declared quantity."
        )

    received_at = timezone.now()

    # --------------------------------------------------------
    # Record confirmed clean linen receipt
    # --------------------------------------------------------

    if received_clean_quantity > 0:
        LinenTransaction.objects.create(
            linen_item=receipt.linen_item,
            quantity=received_clean_quantity,
            condition="CLEAN",
            event_type="RECEIVED_AT_LAUNDRY",
            performed_by=received_by,
            reference=receipt.reference,
            note=note or receipt.note,
        )

    # --------------------------------------------------------
    # Record confirmed dirty linen receipt
    # --------------------------------------------------------

    if received_dirty_quantity > 0:
        LinenTransaction.objects.create(
            linen_item=receipt.linen_item,
            quantity=received_dirty_quantity,
            condition="DIRTY",
            event_type="RECEIVED_AT_LAUNDRY",
            performed_by=received_by,
            reference=receipt.reference,
            note=note or receipt.note,
        )

    receipt.received_by = received_by
    receipt.received_clean_quantity = received_clean_quantity
    receipt.received_dirty_quantity = received_dirty_quantity
    receipt.received_at = received_at
    receipt.status = "RECEIVED"

    if note:
        receipt.note = note

    receipt.save(
        update_fields=[
            "received_by",
            "received_clean_quantity",
            "received_dirty_quantity",
            "received_at",
            "status",
            "note",
        ],
    )

    return receipt