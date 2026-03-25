from decimal import Decimal
from accounting.services.journal import record_transaction_by_slug

def post_inventory_receipt(po):

    hotel = po.department.hotel

    total_value = Decimal("0.00")

    for item in po.items.all():
        total_value += item.base_quantity * item.unit_cost  # ✅ FIXED

    if total_value <= 0:
        return

    record_transaction_by_slug(
        source_slug="inventory-asset",        # DEBIT
        destination_slug="accounts-payable",  # CREDIT
        amount=total_value,
        description=f"Inventory received PO #{po.id}",
        hotel=hotel,
        created_by=po.created_by
    )