from decimal import Decimal
from accounting.services.journal import record_transaction_by_slug

def post_cogs_for_order(order):

    hotel = order.department.hotel

    total_cogs = Decimal("0.00")

    for item in order.items.select_related("menu_item__product"):

        product = item.menu_item.product

        if not product.is_stock_item():
            continue

        cost = product.cost_price * item.quantity
        total_cogs += cost

    if total_cogs <= 0:
        return

    record_transaction_by_slug(
        source_slug="cost-of-goods-sold",   # DEBIT
        destination_slug="finished-goods-inventory",  # ✅ FIXED  # CREDIT
        amount=total_cogs,
        description=f"COGS for POS Order #{order.id}",
        hotel=hotel,
        created_by=order.created_by,
        entry_type="COGS" ,  # ✅ ADD

    )