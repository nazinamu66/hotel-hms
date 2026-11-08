from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from laundry.models import LaundryReceipt, LaundryReceiptDiscrepancy
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

    New returns are held in IN_TRANSIT until Laundry confirms
    the physical count. Legacy receipts retain their original
    Housekeeping-to-Laundry movement and confirmation behavior.
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

    receipt_reference = receipt.reference or f"LAUNDRY-RECEIPT-{receipt.id}"
    is_transit_receipt = LinenTransaction.objects.filter(
        linen_item=receipt.linen_item,
        event_type="SENT_TO_LAUNDRY",
        from_location="HOUSEKEEPING",
        to_location="IN_TRANSIT",
        reference=receipt_reference,
        from_user=receipt.returned_by,
    ).exists()

    clean_difference = receipt.declared_clean_quantity - received_clean_quantity
    dirty_difference = receipt.declared_dirty_quantity - received_dirty_quantity

    # For new workflow returns, confirmed quantities move from transit
    # into Laundry. Any shortfall moves from transit into discrepancy.
    if is_transit_receipt:
        for condition, confirmed, difference in (
            ("CLEAN", received_clean_quantity, clean_difference),
            ("DIRTY", received_dirty_quantity, dirty_difference),
        ):
            if confirmed > 0:
                LinenTransaction.objects.create(
                    linen_item=receipt.linen_item,
                    quantity=confirmed,
                    condition=condition,
                    event_type="RETURNED",
                    from_location="IN_TRANSIT",
                    to_location="LAUNDRY",
                    performed_by=received_by,
                    from_user=receipt.returned_by,
                    reference=receipt_reference,
                    note=note or receipt.note,
                )
            if difference > 0:
                LinenTransaction.objects.create(
                    linen_item=receipt.linen_item,
                    quantity=difference,
                    condition=condition,
                    event_type="RETURNED",
                    from_location="IN_TRANSIT",
                    to_location="DISCREPANCY",
                    performed_by=received_by,
                    from_user=receipt.returned_by,
                    reference=receipt_reference,
                    note=f"Receipt discrepancy pending review. {note or receipt.note}".strip(),
                )
                LaundryReceiptDiscrepancy.objects.create(
                    receipt=receipt,
                    condition=condition,
                    quantity=difference,
                )

    else:
        # Backward compatibility: old records already moved into
        # LAUNDRY at handover. Keep their historical accounting path.
        for condition, difference in (
            ("CLEAN", clean_difference),
            ("DIRTY", dirty_difference),
        ):
            if difference > 0:
                LinenTransaction.objects.create(
                    linen_item=receipt.linen_item,
                    quantity=difference,
                    condition=condition,
                    event_type="RETURNED",
                    from_location="LAUNDRY",
                    to_location="DISCREPANCY",
                    performed_by=received_by,
                    from_user=receipt.returned_by,
                    reference=receipt_reference,
                    note=f"Receipt discrepancy pending review. {note or receipt.note}".strip(),
                )
                LaundryReceiptDiscrepancy.objects.create(
                    receipt=receipt,
                    condition=condition,
                    quantity=difference,
                )

    for condition, difference in (
        ("CLEAN", clean_difference),
        ("DIRTY", dirty_difference),
    ):
        if difference > 0:
            LinenTransaction.objects.create(
                linen_item=receipt.linen_item,
                quantity=difference,
                condition=condition,
                event_type="RETURNED",
                from_location="LAUNDRY",
                to_location="DISCREPANCY",
                performed_by=received_by,
                from_user=receipt.returned_by,
                reference=receipt.reference or f"LAUNDRY-RECEIPT-{receipt.id}",
                note=f"Receipt discrepancy pending review. {note or receipt.note}".strip(),
            )
            LaundryReceiptDiscrepancy.objects.create(
                receipt=receipt,
                condition=condition,
                quantity=difference,
            )

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
            from_user=receipt.returned_by,
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
            from_user=receipt.returned_by,
            reference=receipt.reference,
            note=note or receipt.note,
        )

    receipt.received_by = received_by
    receipt.received_clean_quantity = received_clean_quantity
    receipt.received_dirty_quantity = received_dirty_quantity
    receipt.received_at = received_at
    receipt.status = (
        "DISCREPANCY_PENDING"
        if clean_difference or dirty_difference
        else "RECEIVED"
    )

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

@transaction.atomic
def resolve_laundry_receipt_discrepancy(
    *,
    discrepancy,
    resolution,
    resolved_by,
    note="",
):
    """Resolve a held linen shortfall without silently changing stock."""
    discrepancy = (
        LaundryReceiptDiscrepancy.objects
        .select_for_update()
        .select_related("receipt", "receipt__linen_item")
        .get(pk=discrepancy.pk)
    )

    if discrepancy.status != "OPEN":
        raise ValidationError("This discrepancy has already been resolved.")

    if resolution not in ("RECOVERED", "LOST", "DAMAGED"):
        raise ValidationError("Select a valid discrepancy resolution.")

    receipt = discrepancy.receipt
    reference = receipt.reference or f"LAUNDRY-RECEIPT-{receipt.id}"
    transaction_note = note.strip() or (
        f"Resolution of {discrepancy.condition.lower()} linen discrepancy "
        f"for receipt #{receipt.id}."
    )

    if resolution == "RECOVERED":
        LinenTransaction.objects.create(
            linen_item=receipt.linen_item,
            quantity=discrepancy.quantity,
            condition=discrepancy.condition,
            event_type="RETURNED",
            from_location="DISCREPANCY",
            to_location="LAUNDRY",
            performed_by=resolved_by,
            from_user=receipt.returned_by,
            reference=reference,
            note=transaction_note,
        )
    else:
        LinenTransaction.objects.create(
            linen_item=receipt.linen_item,
            quantity=discrepancy.quantity,
            condition=discrepancy.condition,
            event_type=resolution,
            from_location="DISCREPANCY",
            performed_by=resolved_by,
            reference=reference,
            note=transaction_note,
        )

    discrepancy.status = "RESOLVED"
    discrepancy.resolution = resolution
    discrepancy.resolved_by = resolved_by
    discrepancy.resolved_at = timezone.now()
    discrepancy.resolution_note = transaction_note
    discrepancy.save(update_fields=[
        "status",
        "resolution",
        "resolved_by",
        "resolved_at",
        "resolution_note",
    ])

    receipt.refresh_from_db()
    if not receipt.discrepancies.filter(status="OPEN").exists():
        receipt.status = "DISCREPANCY_RESOLVED"
        receipt.save(update_fields=["status"])

    return discrepancy
