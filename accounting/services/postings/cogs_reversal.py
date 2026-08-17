from accounting.services.journal import record_transaction


def _inventory_system_key(product):

    if product.product_type == "DRINK":
        return "inventory_asset"

    return "finished_goods_inventory"


def reverse_cogs_for_order(order):
    """
    Reverse COGS using the historical cost stored on
    each POS order item.

    Never recalculates costing.
    """

    if not order.is_cogs_posted:
        return

    hotel = order.department.hotel

    for item in order.items.select_related("menu_item__product"):

        product = item.menu_item.product

        if not product.is_stock_item():
            continue

        unit_cost = item.cost_at_sale

        if unit_cost <= 0:
            raise ValueError(
                f"Missing historical cost for {product.name}"
            )

        amount = unit_cost * item.quantity

        record_transaction(
            debit_system_key=_inventory_system_key(product),
            credit_system_key="cost_of_goods_sold",
            amount=amount,
            description=f"COGS REVERSAL for POS Order #{order.id}",
            hotel=hotel,
            created_by=order.created_by,
            entry_type="COGS_REVERSAL",
            reference=f"COGS-REV-{order.id}-{product.id}",
        )

    order.is_cogs_posted = False
    order.save(update_fields=["is_cogs_posted"])