from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

from accounting.services.journal import record_transaction
from accounting.models import Account
from accounting.constants import COMPLIMENTARY_MEALS_EXPENSE


CURRENCY_QUANTUM = Decimal("0.01")


def _money(value):
    """Round a transaction total to the ledger's two-decimal currency scale."""
    return Decimal(value).quantize(CURRENCY_QUANTUM, rounding=ROUND_HALF_UP)


@transaction.atomic
def post_cogs_for_order(order):
    """
    Post COGS using historical FIFO unit costs captured at sale time.

    Aggregate each product class first, then round once at the currency
    boundary. JournalLine stores currency to two decimal places.
    """
    hotel = order.department.hotel
    drink_cogs = Decimal("0")
    food_cogs = Decimal("0")

    for item in order.items.select_related("menu_item__product"):
        product = item.menu_item.product
        if not product.is_stock_item():
            continue

        if item.cost_at_sale <= 0:
            raise ValueError(f"Missing historical cost for {product.name}")

        amount = item.cost_at_sale * item.quantity
        if product.product_type == "DRINK":
            drink_cogs += amount
        elif product.product_type in ("FOOD", "SNACK"):
            food_cogs += amount

    # Round exactly once per ledger transaction. Stable references make
    # retries idempotent through record_transaction().
    drink_cogs = _money(drink_cogs)
    food_cogs = _money(food_cogs)

    if getattr(order, "is_complimentary_breakfast", False):
        # Hotels created before this feature receive the new control account
        # on first use, without changing existing account balances.
        expense, _ = Account.objects.get_or_create(
            hotel=hotel,
            code="5310",
            defaults={
                "name": "Complimentary Meals Expense",
                "account_type": "expense",
                "system_key": COMPLIMENTARY_MEALS_EXPENSE,
                "is_system": True,
                "allow_posting": True,
                "allow_manual_entries": False,
                "is_active": True,
            },
        )
        if (
            expense.system_key != COMPLIMENTARY_MEALS_EXPENSE
            or not expense.is_active
            or not expense.allow_posting
        ):
            raise ValueError(
                "Account code 5310 is already in use. Configure the Complimentary Meals Expense account before redeeming breakfast."
            )
        if drink_cogs > 0:
            record_transaction(
                debit_account=expense,
                credit_system_key="inventory_asset",
                amount=drink_cogs,
                description=f"Complimentary breakfast drink cost for POS Order #{order.id}",
                hotel=hotel,
                created_by=order.created_by,
                entry_type="COMPLIMENTARY_MEAL",
                reference=f"COMP-BREAKFAST-DRINK-COGS-{order.id}",
            )
        if food_cogs > 0:
            record_transaction(
                debit_account=expense,
                credit_system_key="finished_goods_inventory",
                amount=food_cogs,
                description=f"Complimentary breakfast food cost for POS Order #{order.id}",
                hotel=hotel,
                created_by=order.created_by,
                entry_type="COMPLIMENTARY_MEAL",
                reference=f"COMP-BREAKFAST-FOOD-COGS-{order.id}",
            )
        return {"drink_cogs": drink_cogs, "food_cogs": food_cogs}

    if drink_cogs > 0:
        record_transaction(
            debit_system_key="cost_of_goods_sold",
            credit_system_key="inventory_asset",
            amount=drink_cogs,
            description=f"Drink COGS for POS Order #{order.id}",
            hotel=hotel,
            created_by=order.created_by,
            entry_type="COGS",
            reference=f"DRINK-COGS-{order.id}",
        )

    if food_cogs > 0:
        record_transaction(
            debit_system_key="cost_of_goods_sold",
            credit_system_key="finished_goods_inventory",
            amount=food_cogs,
            description=f"Finished food and snack COGS for POS Order #{order.id}",
            hotel=hotel,
            created_by=order.created_by,
            entry_type="COGS",
            reference=f"FOOD-COGS-{order.id}",
        )

    return {"drink_cogs": drink_cogs, "food_cogs": food_cogs}
