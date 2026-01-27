from decimal import Decimal
from django.shortcuts import render, redirect
from django.contrib import messages
from accounts.decorators import role_required
from .models import MenuItem, POSOrder, POSOrderItem
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from inventory.models import Stock
from django.shortcuts import get_object_or_404
from rooms.models import Room
from billing.models import Folio, Payment
from decimal import Decimal



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


from datetime import date
from django.utils.timezone import localdate
from accounts.models import User

@role_required("RESTAURANT", "MANAGER", "ADMIN")
def pos_order_list(request):
    orders = POSOrder.objects.all()

    # 🔒 Restaurant staff restriction
    if request.user.role == "RESTAURANT":
        orders = orders.filter(department=request.user.department)

    # 🔍 Filters
    selected_date = request.GET.get("date")
    staff_id = request.GET.get("staff")
    order_number = request.GET.get("order")
    status = request.GET.get("status")

    # 📅 Date filter
    if selected_date:
        try:
            selected_date = date.fromisoformat(selected_date)
            orders = orders.filter(created_at__date=selected_date)
        except ValueError:
            selected_date = None

    # 👤 Staff filter
    if staff_id:
        orders = orders.filter(created_by_id=staff_id)

    # 🔎 Order number search
    if order_number:
        orders = orders.filter(id__icontains=order_number)

    # 📌 Status filter
    if status:
        orders = orders.filter(status=status)

    orders = orders.order_by("-created_at")

    # Staff list for dropdown
    staff_list = User.objects.filter(
        role="RESTAURANT"
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
    # 1️⃣ Get active menu items
    menu_items = (
        MenuItem.objects
        .filter(is_active=True)
        .select_related("product")
        .order_by("category", "name")
    )

    # 2️⃣ Build stock map for restaurant department
    stocks = Stock.objects.filter(
        department=request.user.department
    ).select_related("product")

    stock_map = {
        stock.product_id: stock.quantity
        for stock in stocks
    }

    # 3️⃣ Filter menu items that actually have stock
    available_items = [
        item for item in menu_items
        if stock_map.get(item.product_id, 0) > 0
    ]

    # 4️⃣ Normalize stock_map to menu_item.id (for template)
    menu_stock_map = {
        item.id: stock_map.get(item.product_id, 0)
        for item in available_items
    }

    # 5️⃣ Get occupied rooms (once)
    occupied_rooms = Room.objects.filter(
        folio__folio_type="ROOM",
        folio__is_closed=False
    ).distinct()

    # 6️⃣ Get cart
    cart = get_cart(request.session)

    # 7️⃣ Calculate total safely
    total = Decimal("0.00")
    for item in cart["items"].values():
        total += Decimal(item["price"]) * item["qty"]

    return render(
        request,
        "restaurant/pos_v2.html",
        {
            "menu_items": available_items,
            "cart": cart,
            "total": total,
            "occupied_rooms": occupied_rooms,
            "stock_map": menu_stock_map,
        }
    )



@role_required("RESTAURANT")
def cart_add(request, item_id):
    cart = get_cart(request.session)
    item = MenuItem.objects.get(id=item_id)

    item_id = str(item_id)

    if item_id not in cart["items"]:
        cart["items"][item_id] = {
            "name": item.name,
            "price": str(item.price),
            "qty": 1,
        }
    else:
        cart["items"][item_id]["qty"] += 1

    request.session.modified = True
    return JsonResponse({"success": True})


@role_required("RESTAURANT")
def cart_update(request, item_id):
    cart = get_cart(request.session)
    item_id = str(item_id)
    qty = int(request.POST.get("qty", 1))

    if item_id in cart["items"]:
        if qty <= 0:
            del cart["items"][item_id]
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



@role_required("RESTAURANT")
@require_POST
def pos_commit(request):
    cart = request.session.get("pos_cart")

    if not cart or not cart.get("items"):
        messages.error(request, "Cart is empty.")
        return redirect("/restaurant/pos-v2/")

    settlement = request.POST.get("settlement")  # PAY_NOW | ROOM
    payment_method = request.POST.get("payment_method")
    room_id = request.POST.get("room")

    room = None
    folio = None
    charge_to_room = False

    # 🔐 ROOM + FOLIO VALIDATION (ONCE, EARLY)
    if settlement == "ROOM":
        if not room_id:
            messages.error(request, "Room must be selected.")
            return redirect("/restaurant/pos-v2/")

        room = get_object_or_404(Room, id=room_id)

        folio = Folio.get_active_room_folio(room)

        if not folio or not folio.guest:
            messages.error(
                request,
                "This room has no checked-in guest. Please check in guest first."
            )
            return redirect("/restaurant/pos-v2/")

        charge_to_room = True

    # 🔐 STOCK VALIDATION
    for item_id, data in cart["items"].items():
        menu_item = get_object_or_404(MenuItem, id=item_id)

        stock = Stock.objects.filter(
            product=menu_item.product,
            department=request.user.department
        ).first()

        if not stock or stock.quantity < data["qty"]:
            messages.error(
                request,
                f"Insufficient stock for {menu_item.name}."
            )
            return redirect("/restaurant/pos-v2/")

    # ✅ CREATE ORDER (NO FOLIO CREATION HERE)
    order = POSOrder.objects.create(
        department=request.user.department,
        created_by=request.user,
        charge_to_room=charge_to_room,
        room=room,
        folio=folio  # ✅ explicit and safe
    )

    # ORDER ITEMS
    for item_id, data in cart["items"].items():
        POSOrderItem.objects.create(
            order=order,
            menu_item_id=item_id,
            quantity=data["qty"],
            price=Decimal(data["price"])
        )

    # CHARGE (creates Charge rows + deducts stock)
    order.charge_order()

    # 💳 PAY NOW
    if settlement == "PAY_NOW":
        Payment.objects.create(
            folio=order.folio,
            amount=order.total_amount,
            method=payment_method or "CASH",
            collected_by=request.user,
            reference=f"POS-{order.id}"
        )
        order.status = "PAID"
        order.save(update_fields=["status"])

    # 🧹 CLEAR CART
    request.session.pop("pos_cart", None)

    messages.success(request, f"Order #{order.id} processed successfully.")
    return redirect("restaurant_order_detail", order_id=order.id)


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


    return render(
        request,
        "restaurant/order_detail.html",
        {"order": order}
    )
