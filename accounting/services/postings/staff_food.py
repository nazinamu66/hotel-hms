from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.constants import STAFF_MEALS_EXPENSE, INVENTORY_ASSET
from accounting.services.journal import record_transaction


@transaction.atomic
def post_staff_food_consumption(*, production, amount, user):
    """Expense the actual FIFO cost of ingredients consumed for staff meals."""
    amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise ValidationError("Staff food must have a positive FIFO cost before accounting can be posted.")

    hotel = production.department.hotel
    return record_transaction(
        debit_system_key=STAFF_MEALS_EXPENSE,
        credit_system_key=INVENTORY_ASSET,
        amount=amount,
        description=(
            f"Staff food - {production.meal_name or 'Daily staff meal'} "
            f"({production.production_date})"
        ),
        hotel=hotel,
        created_by=user,
        entry_type="NORMAL",
        reference=f"STAFF-FOOD-{production.pk}",
    )
