from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.constants import (
    EXPENSE,
    OTHER_INCOME,
    INVENTORY_VARIANCE_EXPENSE,
    INVENTORY_VARIANCE_GAIN,
    INVENTORY_ASSET,
    FINISHED_GOODS_INVENTORY,
)
from accounting.models import Account
from accounting.services.journal import get_system_account, post_journal_entry
from inventory.models import Stock, StockCostLayer, StockMovement, stock_out
from .models import KitchenHandoverReconciliation, KitchenHandoverReconciliationLine


CENT = Decimal("0.01")
ZERO = Decimal("0.000")


def _ensure_variance_accounts(hotel):
    """Create only the two dedicated accounts when an existing hotel lacks them."""
    expense, _ = Account.objects.get_or_create(
        hotel=hotel,
        code="5002",
        defaults={
            "name": "Inventory Variance Expense",
            "account_type": EXPENSE,
            "system_key": INVENTORY_VARIANCE_EXPENSE,
            "is_system": True,
            "allow_posting": True,
            "allow_manual_entries": False,
        },
    )
    gain, _ = Account.objects.get_or_create(
        hotel=hotel,
        code="4300",
        defaults={
            "name": "Inventory Variance Gain",
            "account_type": OTHER_INCOME,
            "system_key": INVENTORY_VARIANCE_GAIN,
            "is_system": True,
            "allow_posting": True,
            "allow_manual_entries": False,
        },
    )
    if (
        expense.system_key != INVENTORY_VARIANCE_EXPENSE
        or gain.system_key != INVENTORY_VARIANCE_GAIN
        or not expense.is_active or not gain.is_active
    ):
        raise ValidationError(
            "The dedicated Inventory Variance accounts are missing, inactive, or have conflicting account codes. "
            "Please ask Accounting to resolve the Chart of Accounts before posting."
        )
    return expense, gain


@transaction.atomic
def post_kitchen_handover_adjustment(*, reconciliation_line, verified_quantity, user):
    """
    Post one approved reconciliation line against the live department stock.
    Counts and all stock quantities are in the product's base unit.
    """
    reconciliation = (
        KitchenHandoverReconciliation.objects.select_for_update()
        .select_related("department__hotel")
        .get(pk=reconciliation_line.reconciliation_id)
    )
    line = (
        KitchenHandoverReconciliationLine.objects.select_for_update()
        .select_related("handover_line__product", "handover_line__location")
        .get(pk=reconciliation_line.pk)
    )

    if reconciliation.status != "APPROVED":
        raise ValidationError("Only an approved reconciliation can be adjusted.")
    if line.adjustment_movement_id or line.adjustment_journal_id or line.adjusted_at:
        raise ValidationError("This discrepancy line has already been adjusted.")
    handover_line = line.handover_line
    product = handover_line.product
    department = reconciliation.department
    if product is None:
        raise ValidationError("Unregistered/manual handover items cannot be adjusted through inventory.")
    if product.hotel_id != department.hotel_id:
        raise ValidationError("The product does not belong to this Kitchen's hotel.")
    if product.product_type not in {"RAW", "FOOD", "DRINK"}:
        raise ValidationError("This product type is not eligible for Kitchen handover adjustment.")

    try:
        physical = Decimal(str(verified_quantity))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Enter a valid fresh physical count.")
    if not physical.is_finite() or physical < 0 or physical.quantize(Decimal("0.001")) != physical:
        raise ValidationError("Physical count must be a non-negative quantity with no more than three decimal places.")

    stock = (
        Stock.objects.select_for_update()
        .filter(product=product, department=department)
        .first()
    )
    book_quantity = stock.quantity if stock else ZERO
    delta = physical - book_quantity
    if delta == 0:
        raise ValidationError("The fresh physical count matches live book stock. No adjustment is required.")

    reference = f"KHR-{reconciliation.pk}-LINE-{line.pk}"
    hotel = department.hotel
    inventory_key = FINISHED_GOODS_INVENTORY if product.product_type == "FOOD" else INVENTORY_ASSET
    inventory_account = get_system_account(hotel, inventory_key)
    variance_expense, variance_gain = _ensure_variance_accounts(hotel)

    if delta < 0:
        quantity = abs(delta)
        result = stock_out(
            product=product,
            department=department,
            quantity=quantity,
            user=user,
            reason=f"Approved Kitchen handover shortage: {reference}",
            reference=reference,
            allow_negative=False,
            allow_uncosted_shortage=False,
        )
        movement = result["movement"]
        cost = Decimal(result["total_cost"]).quantize(CENT)
        if cost <= 0:
            raise ValidationError("FIFO cost is zero. No accounting adjustment was posted.")
        journal = post_journal_entry(
            hotel=hotel,
            description=f"Kitchen handover stock shortage - {product.name}",
            reference=reference,
            entry_type="NORMAL",
            created_by=user,
            lines=[
                {"account": variance_expense, "debit": cost},
                {"account": inventory_account, "credit": cost},
            ],
        )
        unit_cost = (cost / quantity).quantize(Decimal("0.0001"))
    else:
        quantity = delta
        # A gain is valued using the newest positive FIFO layer, never Product.cost_price.
        latest_layer = (
            StockCostLayer.objects.select_for_update()
            .filter(product=product, department=department, quantity_remaining__gt=0)
            .order_by("-received_at", "-pk")
            .first()
        )
        if latest_layer is None or latest_layer.unit_cost <= 0:
            raise ValidationError(
                f"No positive, reliable FIFO cost layer exists for {product.name}. "
                "The surplus cannot be valued and must be investigated by Accounting."
            )
        unit_cost = latest_layer.unit_cost
        cost = (quantity * unit_cost).quantize(CENT)
        if cost <= 0:
            raise ValidationError("The surplus valuation is zero. No adjustment was posted.")

        if stock is None:
            stock = Stock.objects.create(product=product, department=department, quantity=ZERO)
        stock.quantity += quantity
        stock.save(update_fields=["quantity"])
        movement = StockMovement.objects.create(
            product=product,
            to_department=department,
            quantity=quantity,
            movement_type="IN",
            created_by=user,
            reference=reference,
        )
        StockCostLayer.objects.create(
            product=product,
            department=department,
            purchase_item=latest_layer.purchase_item,
            source_reference=reference,
            quantity_received=quantity,
            quantity_remaining=quantity,
            unit_cost=unit_cost,
            is_opening_balance=False,
        )
        journal = post_journal_entry(
            hotel=hotel,
            description=f"Kitchen handover stock surplus - {product.name}",
            reference=reference,
            entry_type="NORMAL",
            created_by=user,
            lines=[
                {"account": inventory_account, "debit": cost},
                {"account": variance_gain, "credit": cost},
            ],
        )

    line.adjustment_movement = movement
    line.adjustment_cost = cost
    line.book_quantity_at_adjustment = book_quantity
    line.adjustment_quantity = quantity
    line.adjustment_unit_cost = unit_cost
    line.adjusted_by = user
    line.adjusted_at = timezone.now()
    line.adjustment_journal = journal
    line.save(update_fields=[
        "adjustment_movement", "adjustment_cost", "book_quantity_at_adjustment",
        "adjustment_quantity", "adjustment_unit_cost", "adjusted_by",
        "adjusted_at", "adjustment_journal",
    ])

    remaining = reconciliation.lines.filter(adjustment_movement__isnull=True).exists()
    if not remaining:
        reconciliation.status = "ADJUSTED"
        reconciliation.save(update_fields=["status"])

    return {
        "movement": movement,
        "journal": journal,
        "quantity": quantity,
        "unit_cost": unit_cost,
        "total_cost": cost,
        "direction": "IN" if delta > 0 else "OUT",
    }
