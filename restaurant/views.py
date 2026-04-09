from decimal import Decimal
from django.shortcuts import render, redirect
from django.contrib import messages
from accounts.decorators import role_required
from .models import MenuItem, POSOrder, POSOrderItem,RestaurantTable
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from inventory.models import Stock
from django.shortcuts import get_object_or_404
from rooms.models import Room
from billing.models import Folio, Payment
from datetime import date
from django.utils.timezone import localdate
from inventory.models import Department
from accounts.models import User
from django.db import models, transaction
from restaurant.services import get_or_create_shift
from kitchen.models import KitchenTicketItem,KitchenTicket
from restaurant.models import Shift
from django.contrib import messages
from django.db.models import Sum
from django.utils import timezone
from accounting.services.postings.cogs import post_cogs_for_order





from billing.services.folio_factory import (
    get_active_room_folio_or_fail,
    get_or_create_walkin_folio,
)


from django.contrib.auth.decorators import user_passes_test

def is_manager(user):
    return user.role in ["MANAGER", "ADMIN"]


@user_passes_test(is_manager)
@require_POST
def refund_order(request, order_id):
    order = get_object_or_404(POSOrder, id=order_id)

    reason = request.POST.get("reason", "")

    try:
        order.refund(user=request.user, reason=reason)
        messages.success(
            request,
            f"Order #{order.id} refunded successfully."
        )
    except Exception as e:
        messages.error(request, str(e))

    return redirect("restaurant_order_detail", order_id=order.id)


@role_required("RESTAURANT", "MANAGER", "ADMIN")
def pos_order_list(request):

    user = request.user

    orders = POSOrder.objects.select_related(
        "created_by",
        "department",
        "folio"
    )

    # 🔒 Restrict restaurant staff to their department
    if user.role == "RESTAURANT":
        orders = orders.filter(department=user.department)

    # 🔍 Filters
    selected_date = request.GET.get("date")
    staff_id = request.GET.get("staff")
    order_number = request.GET.get("order")
    status = request.GET.get("status")

    if selected_date:
        try:
            parsed_date = date.fromisoformat(selected_date)
            orders = orders.filter(created_at__date=parsed_date)
        except ValueError:
            selected_date = None

    if staff_id:
        orders = orders.filter(created_by_id=staff_id)

    if order_number:
        orders = orders.filter(id__icontains=order_number)

    if status:
        orders = orders.filter(status=status)

    orders = orders.order_by("-created_at")

    staff_list = User.objects.filter(
        role="RESTAURANT",
        department=user.department if user.role == "RESTAURANT" else None
    ).order_by("username")

    return render(
        request,
        "restaurant/order_list.html",
        {
            "orders": orders,
            "selected_date": selected_date,
            "staff_list": staff_list,
            "selected_staff": staff_id,
            "order_number": order_number,
            "selected_status": status,
            "status_choices": POSOrder.STATUS_CHOICES,
        }
    )

def get_cart(session):
    if "pos_cart" not in session:
        session["pos_cart"] = {
            "items": {},
            "charge_type": "WALKIN",
            "room_id": None
        }
    return session["pos_cart"]


@role_required("RESTAURANT")
def pos_v2(request):

    user = request.user
    restaurant = user.department

    # --------------------------------
    # Shift guard
    # --------------------------------
    shift = get_or_create_shift(user)

    if shift.status != "OPEN":
        messages.error(request, "Your shift is closed. Please start a new shift.")
        return redirect("dashboard")

    request.session["shift_id"] = shift.id

    # --------------------------------
    # Kitchen department
    # --------------------------------
    kitchen = Department.objects.filter(
        hotel=restaurant.hotel,
        department_type="KITCHEN",
        is_active=True
    ).first()

    if not kitchen:
        messages.error(request, "Restaurant is not linked to a kitchen.")
        return redirect("dashboard")

    # --------------------------------
    # Sellable menu items only
    # --------------------------------
    menu_items = (
        MenuItem.objects
        .filter(
            is_active=True,
            product__usage_type="RESALE"   # 🔥 THE REAL LOGIC
        )
        .select_related("product")
        .order_by("category", "name")
    )

    # --------------------------------
    # Restaurant stock (sellable)
    # --------------------------------
    restaurant_stock_map = {
        s.product_id: s.quantity
        for s in Stock.objects.filter(
            department=restaurant
        )
    }

    # --------------------------------
    # Kitchen stock (reference)
    # --------------------------------
    kitchen_stock_map = {
        s.product_id: s.quantity
        for s in Stock.objects.filter(
            department=kitchen
        )
    }

    # --------------------------------
    # Occupied rooms
    # --------------------------------
    occupied_rooms = Room.objects.filter(
        hotel=restaurant.hotel,
        folio__folio_type="ROOM",
        folio__is_closed=False
    ).distinct()

    # --------------------------------
    # Cart
    # --------------------------------
    cart = get_cart(request.session)

    total = sum(
        Decimal(item["price"]) * item["qty"]
        for item in cart["items"].values()
    )

    # --------------------------------
    # Render
    # --------------------------------
    return render(
        request,
        "restaurant/pos_v2.html",
        {
            "menu_items": menu_items,
            "shift": shift,
            "cart": cart,
            "total": total,
            "restaurant_stock_map": restaurant_stock_map,
            "kitchen_stock_map": kitchen_stock_map,
            "occupied_rooms": occupied_rooms,
        }
    )


@role_required("RESTAURANT")
def cart_add(request, item_id):

    user = request.user
    restaurant = user.department

    # --------------------------------
    # Shift guard
    # --------------------------------
    shift_id = request.session.get("shift_id")

    if not shift_id:
        return JsonResponse({
            "success": False,
            "error": "No active shift."
        })

    shift = Shift.objects.filter(
        id=shift_id,
        user=user,
        department=restaurant,
        status="OPEN"
    ).first()

    if not shift:
        return JsonResponse({
            "success": False,
            "error": "Shift is closed."
        })

    # --------------------------------
    # Cart
    # --------------------------------
    cart = get_cart(request.session)

    menu_item = get_object_or_404(MenuItem, id=item_id)

    # --------------------------------
    # Determine stock department
    # --------------------------------
    # Determine stock department
    if menu_item.product.product_type == "DRINK":
        stock_department = restaurant
    else:
        stock_department = Department.objects.filter(
            hotel=restaurant.hotel,
            department_type="KITCHEN",
            is_active=True
        ).first()

    stock = Stock.objects.filter(
        product=menu_item.product,
        department=stock_department
    ).first()

    available_qty = stock.quantity if stock else 0

    item_id = str(item_id)
    current_qty = cart["items"].get(item_id, {}).get("qty", 0)

    # ❌ DO NOT BLOCK SALES
    if not stock:
        return JsonResponse({
            "success": False,
            "error": "Item not available"
        })

    response = {"success": True}

    if current_qty + 1 > available_qty:
        response["warning"] = f"Low stock ({available_qty} left)"

    # Add to cart
    if item_id not in cart["items"]:
        cart["items"][item_id] = {
            "name": menu_item.name,
            "price": str(menu_item.price),
            "qty": 1,
        }
    else:
        cart["items"][item_id]["qty"] += 1

    request.session.modified = True

    return JsonResponse(response)


@role_required("RESTAURANT")
def cart_update(request, item_id):

    cart = get_cart(request.session)
    restaurant = request.user.department

    item_id = str(item_id)
    qty = int(request.POST.get("qty", 1))

    if item_id not in cart["items"]:
        return JsonResponse({"success": False})

    menu_item = get_object_or_404(MenuItem, id=item_id)

    stock = Stock.objects.filter(
        product=menu_item.product,
        department=restaurant
    ).first()

    available_qty = stock.quantity if stock else 0

    if qty <= 0:
        del cart["items"][item_id]

    elif qty > available_qty:
        return JsonResponse({
            "success": False,
            "error": "Quantity exceeds available stock"
        })

    else:
        cart["items"][item_id]["qty"] = qty

    request.session.modified = True
    return JsonResponse({"success": True})


@role_required("RESTAURANT")
def cart_clear(request):
    request.session["pos_cart"] = {
        "items": {},
        "charge_type": "WALKIN",
        "room_id": None
    }
    return JsonResponse({"success": True})



from django.db import transaction
from decimal import Decimal

@role_required("RESTAURANT")
@require_POST
@transaction.atomic
def pos_commit(request):

    # ------------------------------
    # PREVENT DOUBLE SUBMISSION (SESSION LOCK)
    # ------------------------------
    if request.session.get("pos_processing"):
        messages.warning(request, "Already processing this order.")
        return redirect("/restaurant/pos-v2/")

    try:
        request.session["pos_processing"] = True

        cart = request.session.get("pos_cart")

        if not cart or not cart.get("items"):
            messages.error(request, "Cart is empty.")
            return redirect("/restaurant/pos-v2/")

        settlement = request.POST.get("settlement")
        payment_method = request.POST.get("payment_method")

        restaurant = request.user.department
        hotel = restaurant.hotel

        shift_id = request.session.get("shift_id")
        shift = get_object_or_404(Shift, id=shift_id)

        # ------------------------------
        # RESOLVE KITCHEN
        # ------------------------------
        kitchen = Department.objects.filter(
            hotel=hotel,
            department_type="KITCHEN",
            is_active=True
        ).first()

        if not kitchen:
            messages.error(request, "Kitchen not configured.")
            return redirect("/restaurant/pos-v2/")

        # ------------------------------
        # RESOLVE FOLIO
        # ------------------------------
        try:
            if settlement == "ROOM":
                room_id = request.POST.get("room")
                room = get_object_or_404(Room, id=room_id)
                folio = get_active_room_folio_or_fail(room)
            else:
                folio = get_or_create_walkin_folio(restaurant)

        except ValueError as e:
            messages.error(request, str(e))
            return redirect("/restaurant/pos-v2/")

        # ------------------------------
        # STOCK VALIDATION (LOCKED)
        # ------------------------------
        stock_map = {}
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

            elif product.product_type == "FOOD":
                stock = Stock.objects.select_for_update().filter(
                    product=product,
                    department=kitchen
                ).first()
            else:
                stock = None

            if not stock or stock.quantity < qty:
                messages.error(request, f"Insufficient stock for {product.name}")
                return redirect("/restaurant/pos-v2/")

            stock_map[product.id] = stock

        # ------------------------------
        # CREATE ORDER
        # ------------------------------

        from accounting.utils import get_current_business_day

        business_day = get_current_business_day(hotel)

        order = POSOrder.objects.create(
            department=restaurant,
            created_by=request.user,
            folio=folio,
            shift=shift,
            business_day=business_day   # 🔥 NEW
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
        # CHARGE ORDER
        # ------------------------------
        order.charge_order()
  
        # ------------------------------
        # PAYMENT
        # ------------------------------
        if settlement == "PAY_NOW":
            order.pay_order(payment_method or "CASH")

        # ------------------------------
        # ACCOUNTING
        # ------------------------------
        from accounting.services.journal import record_transaction_by_slug

        if settlement == "PAY_NOW":
            record_transaction_by_slug(
            source_slug="pos-clearing",
            destination_slug="restaurant-revenue",
            amount=order.total_amount,
            description=f"POS Sale #{order.id}",
            hotel=hotel,
            created_by=request.user,
            entry_type="SALE"   # ✅ ADD
        )

        elif settlement == "ROOM":
            record_transaction_by_slug(
                source_slug="accounts-receivable",
                destination_slug="restaurant-revenue",
                amount=order.total_amount,
                description=f"Room POS Order #{order.id}",
                hotel=hotel,
                created_by=request.user
            )

        # ------------------------------
        # CLEAR CART
        # ------------------------------
        request.session.pop("pos_cart", None)

        messages.success(request, f"Order #{order.id} processed successfully.")
        return redirect("restaurant_order_detail", order_id=order.id)

    finally:
        # ALWAYS clear processing flag
        request.session.pop("pos_processing", None)


@role_required("RESTAURANT", "MANAGER", "ADMIN")
def pos_order_detail(request, order_id):

    if request.user.role == "RESTAURANT":
        order = get_object_or_404(
            POSOrder,
            id=order_id,
            department=request.user.department
        )
    else:
        order = get_object_or_404(
            POSOrder,
            id=order_id
        )

    # 🔥 Get kitchen ticket if it exists
    ticket = KitchenTicket.objects.filter(order=order).first()

    return render(
        request,
        "restaurant/order_detail.html",
        {
            "order": order,
            "ticket": ticket,
        }
    )


@role_required("RESTAURANT")
def close_shift(request):

    from accounting.services.journal import record_transaction_by_slug

    user = request.user
    department = user.department

    shift = Shift.objects.filter(
        user=user,
        department=department,
        status="OPEN"
    ).first()

    if not shift:
        messages.error(request, "No open shift found.")
        return redirect("/restaurant/pos-v2/")

    # -----------------------------
    # Orders
    # -----------------------------
    orders = shift.orders.all()

    order_count = orders.count()

    total_sales = orders.aggregate(
        total=Sum("total_amount")
    )["total"] or Decimal("0.00")

    # -----------------------------
    # Payments
    # -----------------------------
    payments = Payment.objects.filter(
        reference__startswith="POS-",
        collected_by=user,
        collected_at__gte=shift.opened_at
    )

    payment_summary = {
        "CASH": Decimal("0.00"),
        "POS": Decimal("0.00"),
        "TRANSFER": Decimal("0.00"),
    }

    for p in payments:
        if p.method in payment_summary:
            payment_summary[p.method] += p.amount

    expected_cash = payment_summary["CASH"]

    # -----------------------------
    # Handle POST (closing shift)
    # -----------------------------
    if request.method == "POST":

        counted_cash = Decimal(request.POST.get("counted_cash", "0"))

        # --------------------------------------
        # ACCOUNTING: MOVE POS → CASH
        # --------------------------------------
        if expected_cash > 0:
            record_transaction_by_slug(
                source_slug="cash",              # Dr Cash
                destination_slug="pos-clearing", # Cr POS Clearing
                amount=expected_cash,
                description=f"Shift close - {shift.id}",
                hotel=department.hotel,
                created_by=user
            )

        # --------------------------------------
        # CLOSE SHIFT
        # --------------------------------------
        shift.closing_cash = counted_cash
        shift.closed_at = timezone.now()
        shift.status = "CLOSED"
        shift.save()

        request.session.pop("shift_id", None)

        messages.success(request, "Shift closed successfully.")

        return redirect("/restaurant/pos-v2/")

    context = {
        "shift": shift,
        "order_count": order_count,
        "total_sales": total_sales,
        "payment_summary": payment_summary,
        "expected_cash": expected_cash,
    }

    return render(
        request,
        "restaurant/close_shift.html",
        context
    )

@role_required("RESTAURANT")
def table_list(request):

    restaurant = request.user.department

    tables = RestaurantTable.objects.filter(
        department=restaurant,
        is_active=True
    ).order_by("name")

    open_orders = POSOrder.objects.filter(
        department=restaurant,
        status="OPEN"
    )

    open_table_ids = set(open_orders.values_list("table_id", flat=True))

    return render(
        request,
        "restaurant/table_list.html",
        {
            "tables": tables,
            "open_table_ids": open_table_ids
        }
    )
@role_required("RESTAURANT")
def restaurant_select_table(request, table_id):

    request.session["table_id"] = table_id

    return redirect("restaurant_pos")