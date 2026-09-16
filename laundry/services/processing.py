from django.core.exceptions import ValidationError
from django.db import transaction

from laundry.models import LaundryProcessing
from laundry.services.materials import consume_laundry_materials

from linen.models import LinenTransaction
from linen.services.balances import get_linen_condition_balance
from accounting.services.postings.laundry import (
    post_laundry_material_consumption,
)


def _process_dirty_linen(
    *,
    linen_item,
    quantity,
    user,
    reference="",
    note="",
):
    """
    Convert dirty linen in Laundry to clean linen.

    This is an internal operation.
    The caller is responsible for the surrounding transaction.
    """

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValidationError(
            "Quantity must be a whole number."
        )

    if quantity <= 0:
        raise ValidationError(
            "Quantity must be greater than zero."
        )

    balance = get_linen_condition_balance(
        linen_item,
        "LAUNDRY",
    )

    dirty_balance = balance["DIRTY"]

    if quantity > dirty_balance:
        raise ValidationError(
            f"Insufficient dirty linen in Laundry. "
            f"Available: {dirty_balance}, requested: {quantity}."
        )

    transaction_record = LinenTransaction.objects.create(
        linen_item=linen_item,
        quantity=quantity,
        condition="CLEAN",
        event_type="PROCESSED",
        from_location="LAUNDRY",
        to_location="LAUNDRY",
        performed_by=user,
        reference=reference,
        note=note,
    )

    return transaction_record


@transaction.atomic
def process_laundry(
    *,
    hotel,
    processing_type,
    user,
    linen_item=None,
    guest_order=None,
    linen_quantity=None,
    materials,
    reference="",
    note="",
):
    """
    Create and complete a Laundry processing event atomically.

    For HOTEL_LINEN processing:
        - linen_item is required
        - linen_quantity is required
        - dirty linen is converted to clean linen
        - a LinenTransaction is created
        - the LinenTransaction is linked to the processing record

    For GUEST_LAUNDRY processing:
        - guest_order is required
        - no linen transaction is created

    In both cases:
        - a LaundryProcessing record is created
        - supplied materials are consumed from Laundry stock
        - FIFO historical cost is recorded
        - any failure rolls back the entire operation
    """

    if processing_type not in (
        "HOTEL_LINEN",
        "GUEST_LAUNDRY",
    ):
        raise ValidationError(
            "Invalid Laundry processing type."
        )

    if processing_type == "HOTEL_LINEN":
        if linen_item is None:
            raise ValidationError(
                "Hotel Linen processing requires a linen item."
            )

        if guest_order is not None:
            raise ValidationError(
                "Hotel Linen processing cannot have a Guest Laundry order."
            )

        if linen_quantity is None:
            raise ValidationError(
                "Hotel Linen processing requires a quantity."
            )

        if linen_item.product.hotel_id != hotel.id:
            raise ValidationError(
                "Linen item must belong to the selected hotel."
            )

    else:
        if guest_order is None:
            raise ValidationError(
                "Guest Laundry processing requires a Guest Laundry order."
            )

        if linen_item is not None:
            raise ValidationError(
                "Guest Laundry processing cannot have a linen item."
            )

        if guest_order.folio.hotel_id != hotel.id:
            raise ValidationError(
                "Guest Laundry order must belong to the selected hotel."
            )
        
        if guest_order.status != "PROCESSING":
            raise ValidationError(
                "Guest Laundry order must be in PROCESSING status."
            )

        if linen_quantity is not None:
            raise ValidationError(
                "Guest Laundry processing cannot have a linen quantity."
            )

    processing = LaundryProcessing.objects.create(
        hotel=hotel,
        processing_type=processing_type,
        linen_item=linen_item,
        guest_order=guest_order,
        performed_by=user,
        reference=reference,
        note=note,
    )

    linen_transaction = None

    if processing_type == "HOTEL_LINEN":
        linen_transaction = _process_dirty_linen(
            linen_item=linen_item,
            quantity=linen_quantity,
            user=user,
            reference=reference,
            note=note,
        )

    material_result = consume_laundry_materials(
        processing=processing,
        materials=materials,
        user=user,
    )

    journal_entry = None

    if material_result["total_cost"] > 0:
        journal_entry = post_laundry_material_consumption(
            processing=processing,
            amount=material_result["total_cost"],
            user=user,
        )

    if linen_transaction is not None:
        processing.linen_transaction = linen_transaction
        processing.save(
            update_fields=["linen_transaction"]
        )

    return {
        "processing": processing,
        "linen_transaction": linen_transaction,
        "materials": material_result["materials"],
        "total_cost": material_result["total_cost"],
        "journal_entry": journal_entry,
    }


# Backward-compatible wrapper for the existing Hotel Linen workflow.
@transaction.atomic
def process_dirty_linen(
    *,
    linen_item,
    quantity,
    user,
    reference="",
    note="",
):
    """
    Existing Hotel Linen processing entry point.

    Kept temporarily so the current UI continues to work
    while the new complete processing workflow is introduced.
    """

    return _process_dirty_linen(
        linen_item=linen_item,
        quantity=quantity,
        user=user,
        reference=reference,
        note=note,
    ).quantity