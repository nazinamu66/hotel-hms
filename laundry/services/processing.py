from django.core.exceptions import ValidationError
from django.db import transaction

from linen.models import LinenTransaction
from linen.services.balances import get_linen_condition_balance


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
    Process dirty linen in Laundry and convert it to clean linen.

    Physical location does not change:

        LAUNDRY / DIRTY -> LAUNDRY / CLEAN
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

    LinenTransaction.objects.create(
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

    return quantity