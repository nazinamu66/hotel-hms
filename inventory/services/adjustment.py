from decimal import Decimal
from django.db import transaction
from accounting.services.journal import post_journal_entry
from accounting.models import Account
from inventory.models import Stock


@transaction.atomic
def apply_stock_adjustment(adjustment):

    stock = Stock.objects.select_for_update().filter(
        product=adjustment.product,
        department=adjustment.department
    ).first()

    if not stock or stock.quantity < adjustment.quantity:
        raise ValueError("Insufficient stock for adjustment")

    # 🔻 reduce stock
    stock.quantity -= adjustment.quantity
    stock.save(update_fields=["quantity"])

    # 💰 accounting
    hotel = adjustment.department.hotel

    inventory = Account.objects.get(hotel=hotel, slug="inventory-asset")
    spoilage = Account.objects.get(hotel=hotel, slug="spoilage-expense")

    amount = adjustment.product.cost_price * adjustment.quantity

    post_journal_entry(
        hotel=hotel,
        description=f"Stock Adjustment - {adjustment.product.name}",
        entry_type="ADJUSTMENT",
        created_by=adjustment.created_by,
        lines=[
            {"account": spoilage, "debit": amount},
            {"account": inventory, "credit": amount},
        ]
    )