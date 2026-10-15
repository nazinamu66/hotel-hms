from decimal import Decimal

from accounting.services.journal import record_transaction


def post_cogs_for_order(order):
    """
    Post COGS using the historical FIFO cost already captured
    on each POS order item at the time of sale.

    Physical inventory and FIFO consumption happen during
    POSOrder.charge_order().
    """

    hotel = order.department.hotel

    drink_cogs = Decimal("0.00")
    food_cogs = Decimal("0.00")

    for item in order.items.select_related("menu_item__product"):

        product = item.menu_item.product

        if not product.is_stock_item():
            continue

        if item.cost_at_sale <= 0:
            raise ValueError(
                f"Missing historical cost for {product.name}"
            )

        amount = item.cost_at_sale * item.quantity

        if product.product_type == "DRINK":
            drink_cogs += amount

        elif product.product_type == "FOOD":
            food_cogs += amount

    # -------------------------
    # DRINK COGS
    # -------------------------
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

    # -------------------------
    # FOOD COGS
    # -------------------------
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

    return {
        "drink_cogs": drink_cogs,
        "food_cogs": food_cogs,
    }