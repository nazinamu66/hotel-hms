from django.db import transaction
from decimal import Decimal

from django.shortcuts import get_object_or_404

from inventory.models import Stock, Department
from restaurant.models import POSOrder, POSOrderItem, Shift
from restaurant.models import MenuItem
from rooms.models import Room
from billing.services.folio_factory import (
    get_active_room_folio_or_fail,
    get_or_create_walkin_folio,
)
from accounting.utils import get_current_business_day
from accounting.services.journal import record_transaction

@transaction.atomic
def process_pos_order(user, cart, settlement, payment_method, room_id=None):

    if not cart or not cart.get("items"):
        raise ValueError("Cart is empty")

    restaurant = user.department
    hotel = restaurant.hotel

    shift = Shift.objects.filter(
        user=user,
        department=restaurant,
        status="OPEN"
    ).first()

    if not shift:
        raise ValueError("No active shift")

    kitchen = Department.objects.filter(
        hotel=hotel,
        department_type="KITCHEN",
        is_active=True
    ).first()

    if not kitchen:
        raise ValueError("Kitchen not configured")

    # ------------------------------
    # RESOLVE FOLIO
    # ------------------------------
    if settlement == "ROOM":
        if not room_id:
            raise ValueError("Room is required")

        room = get_object_or_404(Room, id=room_id)
        folio = get_active_room_folio_or_fail(room)
    else:
        folio = get_or_create_walkin_folio(restaurant)

    # ------------------------------
    # STOCK VALIDATION
    # ------------------------------
    for item_id, data in cart["items"].items():

        menu_item = get_object_or_404(MenuItem, id=item_id)
        product = menu_item.product
        qty = int(data["qty"])

        if not product.is_stock_item():
            continue

        if product.product_type == "DRINK":
            stock = Stock.objects.select_for_update().filter(
                product=product,
                department=restaurant
            ).first()
        else:
            stock = Stock.objects.select_for_update().filter(
                product=product,
                department=kitchen
            ).first()

        if not stock or stock.quantity < qty:
            raise ValueError(f"Insufficient stock for {product.name}")

    # ------------------------------
    # CREATE ORDER
    # ------------------------------
    business_day = get_current_business_day(hotel)

    order = POSOrder.objects.create(
        department=restaurant,
        created_by=user,
        folio=folio,
        shift=shift,
        business_day=business_day
    )

    # ------------------------------
    # ADD ITEMS
    # ------------------------------
    for item_id, data in cart["items"].items():
        POSOrderItem.objects.create(
            order=order,
            menu_item_id=item_id,
            quantity=int(data["qty"]),
            price=Decimal(data["price"])
        )

    # ------------------------------
    # CHARGE & PAYMENT
    # ------------------------------
    order.charge_order()

    if settlement == "PAY_NOW":
        order.pay_order(payment_method or "CASH")

    # ------------------------------
    # ACCOUNTING
    # ------------------------------
    if settlement == "PAY_NOW":

        record_transaction(
            debit_system_key="pos_clearing",
            credit_system_key="restaurant_revenue",
            amount=order.total_amount,
            description=f"POS Sale #{order.id}",
            hotel=hotel,
            created_by=user,
            entry_type="SALE",
            reference=f"POS-{order.id}",
        )

    elif settlement == "ROOM":

        record_transaction(
            debit_system_key="accounts_receivable",
            credit_system_key="restaurant_revenue",
            amount=order.total_amount,
            description=f"Room POS Order #{order.id}",
            hotel=hotel,
            created_by=user,
            entry_type="SALE",
            reference=f"POS-{order.id}",
        )
    return order