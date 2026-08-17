from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from inventory.models import (Department,Stock,StockMovement,)
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

def restore_inventory(order, user, kitchen, restaurant):

    for item in order.items.select_related("menu_item__product"):

        product = item.menu_item.product
        qty = item.quantity

        if product.product_type == "DRINK":
            target_department = restaurant
        else:
            target_department = kitchen

        stock = (
            Stock.objects
            .select_for_update()
            .filter(
                product=product,
                department=target_department
            )
            .first()
        )

        if not stock:
            stock = Stock.objects.create(
                product=product,
                department=target_department,
                quantity=0,
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

@transaction.atomic
def refund_order(order, user, reason=""):

    validate_refund(order, user)

    hotel = order._validate_hotel_integrity()

    restaurant = order.department
    kitchen = get_kitchen_department(restaurant)

    if order.is_cogs_posted:
        restore_inventory(order, user, kitchen, restaurant)
        reverse_cogs_for_order(order)

    reverse_revenue(order, hotel, user)
    reverse_payment(order, user)
    reverse_folio(order)

    finalize_refund(order, user, reason)

    return order
