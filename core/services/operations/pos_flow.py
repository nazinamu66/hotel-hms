from django.db import transaction
from decimal import Decimal
from accounting.models import BankAccount
from django.shortcuts import get_object_or_404
from core.workflows.finalize_order import finalize_order
from core.models import CashHandover
from inventory.models import Stock, Department
from restaurant.models import POSOrder, POSOrderItem, Shift, MenuItem
from rooms.models import Room
from accounting.services.journal import get_system_account
from billing.services.folio_factory import (
    get_active_room_folio_or_fail,
    get_or_create_walkin_folio,
)
from accounting.utils import get_current_business_day
from accounting.services.journal import record_transaction

@transaction.atomic
def process_pos_order(
    user,
    cart,
    settlement,
    payment_method,
    room_id=None,
    bank_account_id=None,
):

    if not cart or not cart.get("items"):
        raise ValueError("Cart is empty")

    restaurant = user.department
    if not restaurant or restaurant.department_type != "RESTAURANT" or not restaurant.hotel_id:
        raise ValueError("Your account must be assigned to a hotel restaurant.")

    hotel = restaurant.hotel

    # Lock the shift so a handover cannot be created concurrently with a POS sale.
    # close_shift() locks this same row before freezing the handover totals.
    shift = Shift.objects.select_for_update().filter(
        user=user,
        department=restaurant,
        status="OPEN"
    ).first()

    if not shift:
        raise ValueError("No active shift")

    if CashHandover.objects.filter(
        shift=shift,
        from_user=user,
        status="PENDING",
    ).exists():
        raise ValueError(
            "A cash handover is pending for this shift. "
            "Complete or cancel it before processing another POS order."
        )

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

        room = get_object_or_404(Room, id=room_id, hotel_id=hotel.pk)
        folio = get_active_room_folio_or_fail(room)
    else:
        folio = get_or_create_walkin_folio(restaurant)

    # ------------------------------
    # STOCK VALIDATION
    # ------------------------------
    for item_id, data in cart["items"].items():

        # Cart data is untrusted: scope menu lookup to this hotel and active resale items.
        menu_item = MenuItem.objects.filter(
            id=item_id,
            is_active=True,
            product__hotel_id=hotel.pk,
            product__usage_type="RESALE",
        ).select_related("product").first()
        if not menu_item:
            raise ValueError("One of the selected menu items is unavailable in this hotel. Refresh the POS and try again.")

        product = menu_item.product
        qty = int(data["qty"])
        if qty <= 0:
            raise ValueError(f"Quantity must be greater than zero for {product.name}.")

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
        # Use the current server-side menu price, never the cached client/session price.
        menu_item = MenuItem.objects.filter(
            id=item_id,
            is_active=True,
            product__hotel_id=hotel.pk,
            product__usage_type="RESALE",
        ).first()
        if not menu_item:
            raise ValueError("One of the selected menu items is unavailable in this hotel. Refresh the POS and try again.")
        POSOrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            quantity=int(data["qty"]),
            price=menu_item.price,
        )

    # ------------------------------
    # CHARGE & COGS
    # ------------------------------
    order.charge_order()
    finalize_order(order)

    # ------------------------------
    # PAYMENT
    # ------------------------------
    if settlement == "PAY_NOW":
        payment_method = payment_method or "CASH"

        if payment_method == "CASH":
            payment_account = get_system_account(
                hotel,
                "cash",
            )

        elif payment_method in ("POS", "TRANSFER"):
            if not bank_account_id:
                raise ValueError(
                    "Bank account is required for POS or Transfer payments."
                )

            bank_account = (
                BankAccount.objects
                .select_related("account")
                .filter(
                    id=bank_account_id,
                    hotel=hotel,
                    is_active=True,
                    account__is_active=True,
                )
                .first()
            )

            if not bank_account:
                raise ValueError(
                    "Selected bank account is not available."
                )

            payment_account = bank_account.account

        else:
            raise ValueError("Invalid payment method.")

        order.pay_order(
            payment_method,
            account=payment_account,
        )

    # ------------------------------
    # ACCOUNTING
    # ------------------------------
    if settlement == "PAY_NOW":

        if payment_method == "CASH":
            record_transaction(
                debit_system_key="cash",
                credit_system_key="restaurant_revenue",
                amount=order.total_amount,
                description=f"POS Sale #{order.id}",
                hotel=hotel,
                created_by=user,
                entry_type="SALE",
                reference=f"POS-{order.id}",
            )

        else:
            record_transaction(
                debit_account=payment_account,
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