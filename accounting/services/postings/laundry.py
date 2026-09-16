from decimal import Decimal

from django.core.exceptions import ValidationError

from accounting.services.journal import record_transaction


def post_laundry_material_consumption(
    *,
    processing,
    amount,
    user,
):
    """
    Post inventory material consumed during Laundry processing.

    Accounting:
        HOTEL_LINEN:
            Debit  Laundry Expense
            Credit Inventory Asset

        GUEST_LAUNDRY:
            Debit  Cost of Goods Sold
            Credit Inventory Asset

    The amount must be the actual FIFO-derived material cost.
    """

    if not processing:
        raise ValidationError(
            "Laundry processing is required."
        )

    if processing.processing_type not in (
        "HOTEL_LINEN",
        "GUEST_LAUNDRY",
    ):
        raise ValidationError(
            "Invalid Laundry processing type."
        )

    amount = Decimal(str(amount))

    if amount <= 0:
        return None

    hotel = processing.hotel

    if processing.processing_type == "HOTEL_LINEN":
        debit_system_key = "laundry_expense"
        entry_type = "LAUNDRY"
        reference = (
            f"LAUNDRY-LINEN-{processing.id}"
        )
        description = (
            f"Hotel linen material consumption "
            f"for Laundry Processing #{processing.id}"
        )

    else:
        debit_system_key = "cost_of_goods_sold"
        entry_type = "COGS"
        reference = (
            f"LAUNDRY-GUEST-{processing.id}"
        )
        description = (
            f"Guest Laundry material consumption "
            f"for Processing #{processing.id}"
        )

    return record_transaction(
        debit_system_key=debit_system_key,
        credit_system_key="inventory_asset",
        amount=amount,
        description=description,
        hotel=hotel,
        created_by=user,
        entry_type=entry_type,
        reference=reference,
    )