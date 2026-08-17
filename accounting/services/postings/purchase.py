from decimal import Decimal

from accounting.services.journal import (
    record_transaction,
)


def post_inventory_receipt(po):

    hotel = po.department.hotel

    total_value = Decimal("0.00")

    for item in po.items.all():
        total_value += (
            item.base_quantity * item.unit_cost
        )

    if total_value <= 0:
        return

    record_transaction(
        debit_system_key="inventory_asset",
        credit_system_key="accounts_payable",
        amount=total_value,
        description=f"Inventory received PO #{po.id}",
        hotel=hotel,
        created_by=po.created_by,
        entry_type="PURCHASE",
        reference=f"PO-{po.id}",
    )