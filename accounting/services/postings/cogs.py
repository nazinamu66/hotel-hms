from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

from accounting.services.journal import record_transaction


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
        elif product.product_type == "FOOD":
            food_cogs += amount

    # Round exactly once per ledger transaction. Stable references make
    # retries idempotent through record_transaction().
    drink_cogs = _money(drink_cogs)
    food_cogs = _money(food_cogs)

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
            description=f"Food COGS for POS Order #{order.id}",
            hotel=hotel,
            created_by=order.created_by,
            entry_type="COGS",
            reference=f"FOOD-COGS-{order.id}",
        )

    return {"drink_cogs": drink_cogs, "food_cogs": food_cogs}
