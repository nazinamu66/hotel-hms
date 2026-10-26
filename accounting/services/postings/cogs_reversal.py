from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

from accounting.services.journal import record_transaction


CURRENCY_QUANTUM = Decimal("0.01")


def _money(value):
    return Decimal(value).quantize(CURRENCY_QUANTUM, rounding=ROUND_HALF_UP)


def reverse_cogs_for_order(order):
    """
    Reverse the same currency-rounded category totals used by COGS posting.
    Uses historical sale-time costs only; never recalculates FIFO costs.
    """
    if not order.is_cogs_posted:
        return {"drink_cogs": Decimal("0.00"), "food_cogs": Decimal("0.00")}

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

    drink_cogs = _money(drink_cogs)
    food_cogs = _money(food_cogs)

    with transaction.atomic():
        if drink_cogs > 0:
            record_transaction(
                debit_system_key="inventory_asset",
                credit_system_key="cost_of_goods_sold",
                amount=drink_cogs,
                description=f"Drink COGS reversal for POS Order #{order.id}",
                hotel=hotel,
                created_by=order.created_by,
                entry_type="COGS_REVERSAL",
                reference=f"COGS-REV-DRINK-{order.id}",
            )

        if food_cogs > 0:
            record_transaction(
                debit_system_key="finished_goods_inventory",
                credit_system_key="cost_of_goods_sold",
                amount=food_cogs,
                description=f"Food COGS reversal for POS Order #{order.id}",
                hotel=hotel,
                created_by=order.created_by,
                entry_type="COGS_REVERSAL",
                reference=f"COGS-REV-FOOD-{order.id}",
            )

        order.is_cogs_posted = False
        order.save(update_fields=["is_cogs_posted"])

    return {"drink_cogs": drink_cogs, "food_cogs": food_cogs}
