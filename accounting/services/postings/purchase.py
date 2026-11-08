from decimal import Decimal

from accounting.services.journal import record_transaction
from accounting.constants import INVENTORY_ASSET, EQUIPMENT_ASSET
from accounting.services.setup_accounts import create_system_accounts


def post_inventory_receipt(receipt):
    """Post only the value physically received on this receipt."""
    po = receipt.purchase_order
    hotel = po.department.hotel
    create_system_accounts(hotel)
    inventory_total = Decimal("0.00")
    equipment_total = Decimal("0.00")

    for line in receipt.items.select_related("purchase_item__product"):
        line_total = line.total_cost
        if line.purchase_item.product.product_type == "EQUIPMENT":
            equipment_total += line_total
        else:
            inventory_total += line_total

    reference_base = f"PO-{po.pk}-REC-{receipt.pk}"
    created_by = receipt.received_by or po.created_by

    if inventory_total > 0:
        record_transaction(
            debit_system_key=INVENTORY_ASSET,
            credit_system_key="accounts_payable",
            amount=inventory_total,
            description=f"Inventory received · PO #{po.pk} · Receipt #{receipt.pk}",
            hotel=hotel,
            created_by=created_by,
            entry_type="PURCHASE",
            reference=f"{reference_base}-INV",
        )

    if equipment_total > 0:
        record_transaction(
            debit_system_key=EQUIPMENT_ASSET,
            credit_system_key="accounts_payable",
            amount=equipment_total,
            description=f"Equipment received · PO #{po.pk} · Receipt #{receipt.pk}",
            hotel=hotel,
            created_by=created_by,
            entry_type="PURCHASE",
            reference=f"{reference_base}-EQUIP",
        )
