from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from inventory.models import (
    Department,
    Stock,
    StockMovement,
    StockCostLayer,
)
from billing.models import (Charge,Payment,)
from accounting.services.journal import record_transaction
from accounting.services.postings.cogs_reversal import (reverse_cogs_for_order,)

def validate_refund(order, user):

    if order.is_refunded:
        raise ValidationError("Order already refunded.")

    if order.status not in ["CHARGED", "PAID"]:
        raise ValidationError(
            "Only charged or paid orders can be refunded."
        )

    if not user:
        raise ValidationError("Refund user required.")
    
def get_kitchen_department(restaurant):
    kitchen = (
        Department.objects
        .filter(
            hotel=restaurant.hotel,
            department_type="KITCHEN",
            is_active=True,
        )
        .first()
    )
    if not kitchen:
        raise ValidationError(
            "Kitchen department not found."
        )
    return kitchen

def restore_inventory(order, user, kitchen, restaurant, *, include_food=True):

    for item in order.items.select_related("menu_item__product"):

        product = item.menu_item.product
        qty = item.quantity

        if not product.is_stock_item():
            continue

        if product.product_type == "DRINK":
            target_department = restaurant

        elif product.product_type == "FOOD":
            if not include_food:
                continue
            target_department = kitchen

        else:
            continue

        stock, _ = (
            Stock.objects
            .select_for_update()
            .get_or_create(
                product=product,
                department=target_department,
                defaults={"quantity": 0},
            )
        )

        stock.quantity += qty
        stock.save(update_fields=["quantity"])

        StockMovement.objects.create(
            product=product,
            to_department=target_department,
            quantity=qty,
            movement_type="IN",
            created_by=user,
            reference=f"POS-REFUND-{order.id}",
        )

        # Restore valuation only when a reliable historical cost exists.
        # Never create a synthetic zero-cost layer for uncosted stock.
        if item.cost_at_sale > 0:
            StockCostLayer.objects.create(
                product=product,
                department=target_department,
                purchase_item=None,
                quantity_received=qty,
                quantity_remaining=qty,
                unit_cost=item.cost_at_sale,
                is_opening_balance=False,
            )

def reverse_revenue(order, hotel, user):
    record_transaction(
        debit_system_key="restaurant_revenue",
        credit_system_key="pos_clearing",
        amount=order.total_amount,
        description=f"Refund for POS Order #{order.id}",
        hotel=hotel,
        created_by=user,
        entry_type="REFUND",
        reference=f"POS-REFUND-{order.id}",
    )

def reverse_payment(order, user):

    if order.status != "PAID":
        return

    Payment.objects.create(
        folio=order.folio,
        amount=-order.total_amount,
        method="REFUND",
        collected_by=user,
        reference=f"POS-REFUND-{order.id}",
    )

def reverse_folio(order):

    Charge.objects.create(
        folio=order.folio,
        description=f"Refund for POS Order #{order.id}",
        department=order.department,
        amount=-order.total_amount,
        reference=f"POS-REFUND-{order.id}",
    )

def finalize_refund(order, user, reason):

    order.is_refunded = True
    order.refunded_at = timezone.now()
    order.refunded_by = user
    order.refund_reason = reason
    order.status = "CANCELLED"

    order.save(
        update_fields=[
            "is_refunded",
            "refunded_at",
            "refunded_by",
            "refund_reason",
            "status",
        ]
    )

def record_refunded_food_waste(order, user, kitchen, reason):
    """Audit prepared food retained as waste without deducting stock twice."""
    from kitchen.models import KitchenSpoilageRecord

    for item in order.items.select_related("menu_item__product"):
        product = item.menu_item.product
        if product.product_type != "FOOD" or not product.is_stock_item():
            continue
        cost = item.cost_at_sale * item.quantity
        KitchenSpoilageRecord.objects.create(
            product=product,
            department=kitchen,
            quantity=item.quantity,
            reason_code="OTHER",
            notes=(
                f"Refunded POS Order #{order.id}; prepared food was not "
                f"returned to usable stock. {reason}".strip()
            ),
            recorded_by=user,
            cost_amount=cost,
            stock_movement=None,
        )


@transaction.atomic
def refund_order(order, user, reason="", *, kitchen_outcome="UNPREPARED_RESTOCK"):

    validate_refund(order, user)
    if kitchen_outcome not in {"UNPREPARED_RESTOCK", "PREPARED_WASTE"}:
        raise ValidationError("A valid Kitchen outcome is required.")

    hotel = order._validate_hotel_integrity()

    restaurant = order.department
    kitchen = get_kitchen_department(restaurant)

    # A successful charge deducts physical stock regardless of whether
    # its accounting COGS posting completed. Restore physical quantities on
    # every valid refund; reverse journal COGS only when it was posted.
    include_food = kitchen_outcome == "UNPREPARED_RESTOCK"
    restore_inventory(
        order, user, kitchen, restaurant, include_food=include_food
    )
    if kitchen_outcome == "PREPARED_WASTE":
        record_refunded_food_waste(order, user, kitchen, reason)
    if order.is_cogs_posted:
        reverse_cogs_for_order(order, include_food=include_food)

    reverse_revenue(order, hotel, user)
    reverse_payment(order, user)
    reverse_folio(order)

    finalize_refund(order, user, reason)

    return order
