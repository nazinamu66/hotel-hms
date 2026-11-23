from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.constants import HOUSEKEEPING_EXPENSE, GUEST_SUPPLIES_EXPENSE
from accounting.models import Account
from accounting.services.journal import record_transaction


MONEY = Decimal("0.01")

EXPENSE_ACCOUNTS = {
    "CLEANING": {
        "code": "5500",
        "name": "Housekeeping Expense",
        "system_key": HOUSEKEEPING_EXPENSE,
    },
    "ROOM_REPLENISHMENT": {
        "code": "5510",
        "name": "Guest Room Supplies Expense",
        "system_key": GUEST_SUPPLIES_EXPENSE,
    },
}


def _get_housekeeping_expense_account(hotel, purpose):
    config = EXPENSE_ACCOUNTS.get(purpose)
    if not config:
        raise ValidationError("Unsupported Housekeeping material expense purpose.")

    account, _ = Account.objects.get_or_create(
        hotel=hotel,
        code=config["code"],
        defaults={
            "name": config["name"],
            "account_type": "expense",
            "system_key": config["system_key"],
            "is_system": True,
            "allow_posting": True,
            "allow_manual_entries": False,
            "is_active": True,
        },
    )
    if (
        account.name != config["name"]
        or account.account_type != "expense"
        or account.system_key != config["system_key"]
        or not account.is_system
        or not account.is_active
        or not account.allow_posting
    ):
        raise ValidationError(
            f"Account code {config['code']} is already in use or misconfigured. "
            f"Configure the {config['name']} system account before consuming Housekeeping materials."
        )
    return account


@transaction.atomic
def post_housekeeping_material_consumption(
    *,
    assignment,
    product,
    purpose,
    stock_result,
    user,
):
    """
    Recognize Housekeeping material consumption using the exact FIFO cost
    returned by inventory.stock_out().

    CLEANING:
        Dr Housekeeping Expense
        Cr Inventory Asset

    ROOM_REPLENISHMENT:
        Dr Guest Room Supplies Expense
        Cr Inventory Asset

    The stock movement ID makes retries idempotent. A reservation/issue or
    a pending return does not post accounting; only actual consumption does.
    """
    if purpose not in EXPENSE_ACCOUNTS:
        raise ValidationError("Unsupported Housekeeping material expense purpose.")

    movement = stock_result.get("movement")
    if movement is None or not movement.pk:
        raise ValidationError("Housekeeping stock movement is required for accounting.")

    amount = Decimal(str(stock_result.get("total_cost", 0))).quantize(
        MONEY, rounding=ROUND_HALF_UP
    )
    if amount <= 0:
        raise ValidationError(
            f"Cannot post {product.name}: its FIFO consumption cost is zero. "
            "Check the Housekeeping stock cost layers before retrying."
        )

    hotel = assignment.room.hotel
    expense_account = _get_housekeeping_expense_account(hotel, purpose)
    purpose_label = (
        "cleaning materials" if purpose == "CLEANING"
        else "complimentary guest-room supplies"
    )

    return record_transaction(
        debit_account=expense_account,
        credit_system_key="inventory_asset",
        amount=amount,
        description=(
            f"Housekeeping {purpose_label} - Room "
            f"{assignment.room.room_number} - {product.name}"
        ),
        hotel=hotel,
        created_by=user,
        entry_type="NORMAL",
        reference=f"HK-MATERIAL-MOVEMENT-{movement.pk}",
    )
