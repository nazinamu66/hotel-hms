from decimal import Decimal

from accounting.services.journal import record_transaction
from accounting.constants import INVENTORY_ASSET, EQUIPMENT_ASSET


def post_inventory_receipt(po):
    hotel = po.department.hotel
    inventory_total = Decimal("0.00")
    equipment_total = Decimal("0.00")

    for item in po.items.select_related("product"):
        line_total = item.purchase_quantity * item.unit_cost
        if item.product.product_type == "EQUIPMENT":
            equipment_total += line_total
        else:
            inventory_total += line_total

    if inventory_total > 0:
        record_transaction(
            debit_system_key=INVENTORY_ASSET,
            credit_system_key="accounts_payable",
            amount=inventory_total,
            description=f"Inventory received PO #{po.id}",
            hotel=hotel,
            created_by=po.created_by,
            entry_type="PURCHASE",
            reference=f"PO-{po.id}-INV",
        )

    if equipment_total > 0:
        record_transaction(
            debit_system_key=EQUIPMENT_ASSET,
            credit_system_key="accounts_payable",
            amount=equipment_total,
            description=f"Equipment received PO #{po.id}",
            hotel=hotel,
            created_by=po.created_by,
            entry_type="PURCHASE",
            reference=f"PO-{po.id}-EQUIP",
        )
