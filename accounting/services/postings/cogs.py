from decimal import Decimal
from accounting.services.journal import record_transaction
from inventory.services.costing import get_latest_cost


def _get_drink_cost(product):
    return (
        product.purchase_cost
        or product.cost_price
        or Decimal("0.00")
    )


def _get_food_cost(product, kitchen):

    cost = get_latest_cost(product, kitchen)

    if cost <= 0:
        raise ValueError(
            f"No production cost found for {product.name}"
        )

    return cost


def _save_cost_snapshot(item, unit_cost):

    item.cost_at_sale = unit_cost
    item.save(update_fields=["cost_at_sale"])


def post_cogs_for_order(order):
    """
    Post Cost of Goods Sold for a served POS order.

    - Drinks credit Inventory Asset.
    - Food credits Finished Goods Inventory.
    - Saves historical cost snapshot on every order item.
    """

    hotel = order.department.hotel

    drink_cogs = Decimal("0.00")
    food_cogs = Decimal("0.00")

    for item in order.items.select_related("menu_item__product"):

        product = item.menu_item.product

        if not product.is_stock_item():
            continue

        # -------------------------
        # Determine historical cost
        # -------------------------

        if product.product_type == "DRINK":

            unit_cost = _get_drink_cost(product)
            drink_cogs += unit_cost * item.quantity

        elif product.product_type == "FOOD":

            unit_cost = _get_food_cost(
                product,
                order.department
            )

            food_cogs += unit_cost * item.quantity

        else:
            continue

        _save_cost_snapshot(item, unit_cost)

    # -------------------------
    # Drinks
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
    # Food
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