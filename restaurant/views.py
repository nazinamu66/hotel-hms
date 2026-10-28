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
from inventory.models import Department
from accounts.models import User
from django.db import models, transaction
from kitchen.models import KitchenTicketItem,KitchenTicket
from restaurant.models import Shift
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from django.db.models import OuterRef, Subquery, Sum, DecimalField, Value,Q
from django.db.models.functions import Coalesce
from core.models import CashHandover
from core.services.cash.handover import create_handover
from accounting.models import BankAccount
from accounts.services.access import get_accessible_hotels





from billing.services.folio_factory import (
    get_active_room_folio_or_fail,
    get_or_create_walkin_folio,
)


from django.contrib.auth.decorators import user_passes_test

def is_manager(user):
    return user.role in ["MANAGER", "ADMIN"]



@role_required("RESTAURANT", "MANAGER", "ADMIN")
def pos_order_list(request):
    user = request.user
    orders = POSOrder.objects.select_related(
        "created_by", "department", "department__hotel", "folio", "shift"
    )

    # Enforce hotel/department boundaries before applying user-supplied filters.
    if user.role == "RESTAURANT":
        if not user.department_id:
            raise PermissionDenied("Restaurant staff must be assigned to a department.")
        orders = orders.filter(department_id=user.department_id)
        staff_list = User.objects.filter(
            role="RESTAURANT", department_id=user.department_id, is_active=True
        )
    else:
        accessible_hotels = get_accessible_hotels(user)
        orders = orders.filter(department__hotel__in=accessible_hotels)
        staff_list = User.objects.filter(
            role="RESTAURANT", department__hotel__in=accessible_hotels, is_active=True
        )

    selected_date = request.GET.get("date", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    staff_id = request.GET.get("staff", "").strip()
    order_number = request.GET.get("order", "").strip()
    status = request.GET.get("status", "").strip()

    if selected_date:
        try:
            orders = orders.filter(created_at__date=date.fromisoformat(selected_date))
        except ValueError:
            selected_date = ""

    if date_from:
        try:
            orders = orders.filter(created_at__date__gte=date.fromisoformat(date_from))
        except ValueError:
            date_from = ""

    if date_to:
        try:
            orders = orders.filter(created_at__date__lte=date.fromisoformat(date_to))
        except ValueError:
            date_to = ""

    if staff_id and staff_id.isdigit():
        orders = orders.filter(created_by_id=int(staff_id))
    else:
        staff_id = ""

    if order_number:
        if order_number.isdigit():
            orders = orders.filter(id=int(order_number))
        else:
            order_number = ""

    valid_statuses = {choice[0] for choice in POSOrder.STATUS_CHOICES}
    if status in valid_statuses:
        orders = orders.filter(status=status)
    else:
        status = ""

    orders = orders.order_by("-created_at", "-id")

    return render(
        request,
        "restaurant/order_list.html",
        {
            "orders": orders,
            "selected_date": selected_date,
            "date_from": date_from,
            "date_to": date_to,
            "staff_list": staff_list.order_by("username"),
            "selected_staff": staff_id,
            "order_number": order_number,
            "selected_status": status,
            "status_choices": POSOrder.STATUS_CHOICES,
            "result_count": orders.count(),
        },
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
def ready_food_alerts(request):
    department = request.user.department
    if not department or department.department_type != "RESTAURANT":
        raise PermissionDenied("Restaurant department access is required.")
    tickets = KitchenTicket.objects.filter(status="READY", order__department__hotel_id=department.hotel_id, items__menu_item__product__product_type="FOOD").select_related("order", "order__table", "room").prefetch_related("items__menu_item__product").distinct().order_by("created_at")
    payload = []
    for ticket in tickets:
        payload.append({"id": ticket.id, "order_id": ticket.order_id, "location": (f"Table {ticket.order.table.name}" if ticket.order.table_id else f"Room {ticket.room.room_number}" if ticket.room_id else "Walk-in"), "items": [{"name": item.menu_item.name, "quantity": item.quantity} for item in ticket.items.all() if item.menu_item.product.product_type == "FOOD"]})
    return JsonResponse({"tickets": payload})


@role_required("RESTAURANT")
def ready_food_ticket_print(request, ticket_id):
    """Printable 80mm kitchen collection slip, scoped to the user's hotel."""
    department = request.user.department
    if not department or department.department_type != "RESTAURANT":
        raise PermissionDenied("Restaurant department access is required.")
    ticket = get_object_or_404(
        KitchenTicket.objects.select_related(
            "order", "order__department__hotel", "order__table", "room", "accepted_by"
        ).prefetch_related("items__menu_item__product"),
        pk=ticket_id,
        status="READY",
        order__department__hotel_id=department.hotel_id,
        items__menu_item__product__product_type="FOOD",
    )
    food_items = [
        item for item in ticket.items.all()
        if item.menu_item.product.product_type == "FOOD"
    ]
    location = (
        f"Table {ticket.order.table.name}" if ticket.order.table_id
        else f"Room {ticket.room.room_number}" if ticket.room_id
        else "Walk-in"
    )
    return render(request, "restaurant/ready_food_ticket_print.html", {
        "ticket": ticket,
        "order": ticket.order,
        "hotel": ticket.order.department.hotel,
        "food_items": food_items,
        "location": location,
        "printed_by": request.user,
    })


@role_required("RESTAURANT")
def pos_v2(request):

    user = request.user
    restaurant = user.department

    # --------------------------------
    # Pending shift handover guard
    # --------------------------------
    pending_shift_handover = CashHandover.objects.filter(
        to_user=user,
        custody_type="SHIFT",
        status="PENDING",
    ).order_by("-created_at").first()

    if pending_shift_handover:
        messages.info(
            request,
            "You have a pending shift handover. Accept it before starting a new shift."
        )
        return redirect("cash_handover_list")

    # --------------------------------
    # Shift guard
    # --------------------------------
    shift = Shift.objects.filter(
        user=user,
        department=restaurant,
        status="OPEN",
    ).first()

    if not shift:
        messages.info(
            request,
            "You do not have an open shift. Please open a shift before using the POS."
        )
        return redirect("restaurant_shift_open")

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
        messages.error(
            request,
            "Restaurant is not linked to a kitchen."
        )
        return redirect("dashboard")

    # --------------------------------
    # FOOD MENU
    # --------------------------------
    food_items = (
        MenuItem.objects
        .filter(
            is_active=True,
            category="FOOD",
            product__usage_type="RESALE",
        )
        .select_related("product")
        .order_by("name")
    )

    # --------------------------------
    # DRINK MENU
    # --------------------------------
    drink_items = (
        MenuItem.objects
        .filter(
            is_active=True,
            category="DRINK",
            product__usage_type="RESALE",
        )
        .select_related("product")
        .order_by("name")
    )

    # --------------------------------
    # Restaurant stock
    # --------------------------------
    restaurant_stock_map = {
        stock.product_id: stock.quantity
        for stock in Stock.objects.filter(
            department=restaurant
        )
    }

    # --------------------------------
    # Kitchen stock
    # --------------------------------
    kitchen_stock_map = {
        stock.product_id: stock.quantity
        for stock in Stock.objects.filter(
            department=kitchen
        )
    }

    # --------------------------------
    # Occupied rooms
    # --------------------------------
    occupied_rooms = (
        Room.objects
        .filter(
            hotel=restaurant.hotel,
            folio__folio_type="ROOM",
            folio__is_closed=False
        )
        .distinct()
    )

    # --------------------------------
    # Bank accounts
    # --------------------------------
    bank_accounts = (
        BankAccount.objects
        .filter(
            hotel=restaurant.hotel,
            is_active=True,
            account__is_active=True,
        )
        .select_related("account")
        .order_by(
            "bank_name",
            "account_name"
        )
    )

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
            "food_items": food_items,
            "drink_items": drink_items,

            "shift": shift,
            "cart": cart,
            "total": total,

            "restaurant_stock_map": restaurant_stock_map,
            "kitchen_stock_map": kitchen_stock_map,

            "occupied_rooms": occupied_rooms,
            "bank_accounts": bank_accounts,
        }
    )

@role_required("RESTAURANT")
@require_POST
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

    if not restaurant or not restaurant.hotel_id:
        return JsonResponse({"success": False, "error": "Your account is not assigned to a hotel restaurant."}, status=403)

    menu_item = get_object_or_404(
        MenuItem.objects.select_related("product", "product__hotel"),
        id=item_id,
        is_active=True,
        product__hotel_id=restaurant.hotel_id,
    )

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
@require_POST
def cart_update(request, item_id):

    cart = get_cart(request.session)
    restaurant = request.user.department

    item_id = str(item_id)
    qty = int(request.POST.get("qty", 1))

    if item_id not in cart["items"]:
        return JsonResponse({"success": False})

    if not restaurant or not restaurant.hotel_id:
        return JsonResponse({"success": False, "error": "Your account is not assigned to a hotel restaurant."}, status=403)

    menu_item = get_object_or_404(
        MenuItem.objects.select_related("product", "product__hotel"),
        id=item_id,
        is_active=True,
        product__hotel_id=restaurant.hotel_id,
    )

    if menu_item.product.product_type == "DRINK":
        stock_department = restaurant
    else:
        stock_department = Department.objects.filter(
            hotel=restaurant.hotel,
            department_type="KITCHEN",
            is_active=True,
        ).first()

    stock = Stock.objects.filter(
        product=menu_item.product,
        department=stock_department,
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
@require_POST
def cart_clear(request):
    request.session["pos_cart"] = {
        "items": {},
        "charge_type": "WALKIN",
        "room_id": None
    }
    return JsonResponse({"success": True})



from django.db import transaction
from decimal import Decimal
from core.services.operations.pos_flow import process_pos_order

@role_required("RESTAURANT")
@require_POST
@transaction.atomic
def pos_commit(request):

    cart = request.session.get("pos_cart")

    try:
        order = process_pos_order(
        user=request.user,
        cart=cart,
        settlement=request.POST.get("settlement"),
        payment_method=request.POST.get("payment_method"),
        room_id=request.POST.get("room"),
        bank_account_id=request.POST.get("bank_account"),
    )

        request.session.pop("pos_cart", None)

        messages.success(request, f"Order #{order.id} processed successfully.")
        return redirect("restaurant_order_detail", order_id=order.id)

    except Exception as e:
        messages.error(request, str(e))
        return redirect("/restaurant/pos-v2/")



@role_required("RESTAURANT", "MANAGER", "ADMIN")
def pos_order_receipt(request, order_id):
    if request.user.role == "RESTAURANT":
        if not request.user.department_id:
            raise PermissionDenied("Restaurant staff must be assigned to a department.")
        order = get_object_or_404(
            POSOrder.objects.select_related(
                "department__hotel", "created_by", "folio__room", "folio__guest", "shift",
            ).prefetch_related("items__menu_item"),
            id=order_id,
            department=request.user.department,
        )
    else:
        order = get_object_or_404(
            POSOrder.objects.select_related(
                "department__hotel", "created_by", "folio__room", "folio__guest", "shift",
            ).prefetch_related("items__menu_item"),
            id=order_id,
            department__hotel__in=get_accessible_hotels(request.user),
        )

    hotel = order.department.hotel
    from core.models import BusinessProfile
    business_profile = BusinessProfile.objects.filter(hotel=hotel).first()
    organization = hotel.organization
    receipt_items = [
        {
            "name": item.menu_item.name,
            "quantity": item.quantity,
            "unit_price": item.price,
            "line_total": item.quantity * item.price,
        }
        for item in order.items.all()
    ]

    payment = Payment.objects.filter(
        reference=f"POS-{order.id}",
    ).order_by("-collected_at").first()
    if order.folio.folio_type == "ROOM":
        payment_label = "ROOM CHARGE"
    elif payment:
        payment_label = payment.get_method_display()
    else:
        payment_label = "NOT RECORDED"

    if order.status == "OPEN":
        payment_label = "UNSETTLED"
    elif order.status == "CANCELLED":
        payment_label = "CANCELLED"

    return render(request, "restaurant/receipt.html", {
        "order": order,
        "hotel": hotel,
        "organization": organization,
        "business_profile": business_profile,
        "receipt_items": receipt_items,
        "payment_label": payment_label,
        "generated_at": timezone.now(),
        "currency": business_profile.currency if business_profile else "₦",
    })


@role_required("RESTAURANT", "MANAGER", "ADMIN")
def pos_order_detail(request, order_id):
    if request.user.role == "RESTAURANT":
        order = get_object_or_404(
            POSOrder,
            id=order_id,
            department=request.user.department,
        )
    else:
        order = get_object_or_404(
            POSOrder.objects.select_related("department__hotel"),
            id=order_id,
            department__hotel__in=get_accessible_hotels(request.user),
        )

    ticket = KitchenTicket.objects.filter(
        order=order
    ).first()

    from billing.models import RefundRequest

    refund_request = (
        RefundRequest.objects
        .filter(
            source_type="POS_ORDER",
            pos_order=order,
        )
        .order_by("-requested_at")
        .first()
    )

    return render(
        request,
        "restaurant/order_detail.html",
        {
            "order": order,
            "ticket": ticket,
            "refund_request": refund_request,
        },
    )


@role_required("RESTAURANT")
@transaction.atomic
def close_shift(request):
    user = request.user
    department = user.department

    shift = (
        Shift.objects
        .select_for_update()
        .filter(
            user=user,
            department=department,
            status="OPEN",
        )
        .first()
    )

    if not shift:
        messages.error(request, "No open shift found.")
        return redirect("/restaurant/pos-v2/")

    # --------------------------------------------------
    # Prevent duplicate pending handovers
    # --------------------------------------------------
    pending_handover = (
        CashHandover.objects
        .filter(
            shift=shift,
            status="PENDING",
        )
        .first()
    )

    # --------------------------------------------------
    # Orders
    # --------------------------------------------------
    orders = shift.orders.all()

    order_count = orders.count()

    settled_orders = orders.filter(status__in=["CHARGED", "PAID"])

    total_sales = (
        settled_orders.aggregate(total=Sum("total_amount"))["total"]
        or Decimal("0.00")
    )
    walkin_sales = (
        settled_orders.filter(folio__folio_type="WALKIN")
        .aggregate(total=Sum("total_amount"))["total"]
        or Decimal("0.00")
    )
    room_charge_sales = (
        settled_orders.filter(folio__folio_type="ROOM")
        .aggregate(total=Sum("total_amount"))["total"]
        or Decimal("0.00")
    )
    unsettled_orders = orders.filter(status="OPEN").count()

    # --------------------------------------------------
    # Payments
    # --------------------------------------------------
    payments = (
        Payment.objects
        .filter(shift=shift)
        .order_by("-collected_at")
    )

    payment_summary = {
        "CASH": Decimal("0.00"),
        "POS": Decimal("0.00"),
        "TRANSFER": Decimal("0.00"),
        "REFUND": Decimal("0.00"),
    }

    for payment in payments:
        if payment.method in payment_summary:
            payment_summary[payment.method] += payment.amount

    expected_cash = (
        shift.opening_cash
        + payment_summary["CASH"]
    )

    # --------------------------------------------------
    # Recipient options
    # --------------------------------------------------
    recipients = (
        User.objects
        .filter(
            is_active=True,
        )
        .filter(
            Q(
                role="RESTAURANT",
                department=department,
            )
            |
            Q(
                role__in=["MANAGER", "ACCOUNTANT"],
                hotel=department.hotel,
            )
        )
        .exclude(id=user.id)
        .order_by("role", "username")
    )

    # --------------------------------------------------
    # Create cash handover
    # --------------------------------------------------
    if request.method == "POST":

        if unsettled_orders:
            messages.error(
                request,
                f"Cannot hand over this shift while {unsettled_orders} order(s) remain open. Resolve them first."
            )
            return render(
                request,
                "restaurant/close_shift.html",
                {
                    "shift": shift,
                    "order_count": order_count,
                    "total_sales": total_sales,
                    "walkin_sales": walkin_sales,
                    "room_charge_sales": room_charge_sales,
                    "unsettled_orders": unsettled_orders,
                    "payment_summary": payment_summary,
                    "expected_cash": expected_cash,
                    "recipients": recipients,
                    "pending_handover": pending_handover,
                },
            )

        if pending_handover:
            messages.error(
                request,
                "This shift already has a pending cash handover."
            )
            return redirect(
                "restaurant_shift_detail",
                shift_id=shift.id,
            )

        recipient_id = request.POST.get("recipient")
        sender_count_raw = request.POST.get("sender_count", "").strip()

        if not recipient_id:
            messages.error(
                request,
                "Please select the person receiving the cash."
            )
            return render(
                request,
                "restaurant/close_shift.html",
                {
                    "shift": shift,
                    "order_count": order_count,
                    "total_sales": total_sales,
                    "walkin_sales": walkin_sales,
                    "room_charge_sales": room_charge_sales,
                    "unsettled_orders": unsettled_orders,
                    "payment_summary": payment_summary,
                    "expected_cash": expected_cash,
                    "recipients": recipients,
                },
            )

        if not sender_count_raw:
            messages.error(
                request,
                "Please enter the cash amount you counted."
            )
            return render(
                request,
                "restaurant/close_shift.html",
                {
                    "shift": shift,
                    "order_count": order_count,
                    "total_sales": total_sales,
                    "payment_summary": payment_summary,
                    "expected_cash": expected_cash,
                    "recipients": recipients,
                },
            )

        try:
            sender_count = Decimal(sender_count_raw)
        except Exception:
            messages.error(
                request,
                "Please enter a valid cash amount."
            )
            return render(
                request,
                "restaurant/close_shift.html",
                {
                    "shift": shift,
                    "order_count": order_count,
                    "total_sales": total_sales,
                    "payment_summary": payment_summary,
                    "expected_cash": expected_cash,
                    "recipients": recipients,
                },
            )

        if sender_count < 0:
            messages.error(
                request,
                "Cash counted cannot be negative."
            )
            return render(
                request,
                "restaurant/close_shift.html",
                {
                    "shift": shift,
                    "order_count": order_count,
                    "total_sales": total_sales,
                    "payment_summary": payment_summary,
                    "expected_cash": expected_cash,
                    "recipients": recipients,
                },
            )

        recipient = get_object_or_404(
            User,
            id=recipient_id,
            is_active=True,
        )

        if recipient.role == "RESTAURANT":
            custody_type = "SHIFT"
        else:
            custody_type = "CUSTODY"

        try:
            handover = create_handover(
            from_user=user,
            to_user=recipient,
            department=department,
            expected_amount=expected_cash,
            sender_count=sender_count,
            shift=shift,
            custody_type=custody_type,
        )
        except Exception as e:
            messages.error(request, str(e))
            return render(
                request,
                "restaurant/close_shift.html",
                {
                    "shift": shift,
                    "order_count": order_count,
                    "total_sales": total_sales,
                    "payment_summary": payment_summary,
                    "expected_cash": expected_cash,
                    "recipients": recipients,
                },
            )

        messages.success(
            request,
            f"Cash handover #{handover.id} created. "
            f"{recipient.get_full_name() or recipient.username} "
            "must independently count and accept the cash before the shift closes."
        )

        return redirect(
            "restaurant_shift_detail",
            shift_id=shift.id,
        )

    return render(
        request,
        "restaurant/close_shift.html",
        {
            "shift": shift,
            "order_count": order_count,
            "total_sales": total_sales,
            "walkin_sales": walkin_sales,
            "room_charge_sales": room_charge_sales,
            "unsettled_orders": unsettled_orders,
            "payment_summary": payment_summary,
            "expected_cash": expected_cash,
            "recipients": recipients,
            "pending_handover": pending_handover,
        },
    )

@role_required("RESTAURANT")
@transaction.atomic
def open_shift(request):
    user = request.user
    department = user.department

    if not department:
        messages.error(request, "You are not assigned to a department.")
        return redirect("dashboard")

    # Restaurant only
    if department.department_type != "RESTAURANT":
        messages.error(request, "This action is only available to Restaurant staff.")
        return redirect("dashboard")

    # --------------------------------------------------
    # Pending shift handover guard
    # --------------------------------------------------
    pending_handover = (
        CashHandover.objects
        .filter(
            to_user=user,
            custody_type="SHIFT",
            status="PENDING",
        )
        .first()
    )

    if pending_handover:
        messages.info(
            request,
            "You have a pending shift handover. Accept it before opening a shift."
        )
        return redirect("cash_handover_list")

    # --------------------------------------------------
    # User already has an open shift
    # --------------------------------------------------
    existing_user_shift = (
        Shift.objects
        .filter(
            user=user,
            department=department,
            status="OPEN",
        )
        .first()
    )

    if existing_user_shift:
        messages.info(request, "You already have an open shift.")
        return redirect("restaurant_pos")

    # --------------------------------------------------
    # Restaurant allows only ONE open shift
    # --------------------------------------------------
    existing_shift = (
        Shift.objects
        .select_related("user")
        .filter(
            department=department,
            status="OPEN",
        )
        .first()
    )

    if existing_shift:
        messages.error(
            request,
            f"Restaurant already has an open shift handled by "
            f"{existing_shift.user.username}."
        )
        return redirect("restaurant_shift_list")

    # --------------------------------------------------
    # Open shift
    # --------------------------------------------------
    if request.method == "POST":
        opening_cash_raw = request.POST.get("opening_cash", "").strip()

        try:
            opening_cash = Decimal(opening_cash_raw)
        except Exception:
            messages.error(request, "Enter a valid opening cash amount.")
            return render(
                request,
                "restaurant/open_shift.html",
                {"opening_cash": opening_cash_raw},
            )

        if opening_cash < 0:
            messages.error(request, "Opening cash cannot be negative.")
            return render(
                request,
                "restaurant/open_shift.html",
                {"opening_cash": opening_cash_raw},
            )

        shift = Shift.objects.create(
            user=user,
            department=department,
            opening_cash=opening_cash,
            status="OPEN",
        )

        request.session["shift_id"] = shift.id

        messages.success(
            request,
            f"Shift opened successfully with ₦{opening_cash:,.2f} opening cash."
        )

        return redirect("restaurant_pos")

    return render(
        request,
        "restaurant/open_shift.html",
        {
            "opening_cash": "0.00",
        },
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


@role_required("RESTAURANT", "MANAGER", "DIRECTOR", "ADMIN")
def shift_list(request):
    user = request.user

    decimal_field = DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    sales_subquery = (
        POSOrder.objects
        .filter(
            shift_id=OuterRef("pk"),
            status__in=["CHARGED", "PAID"],
        )
        .values("shift_id")
        .annotate(
            total=Sum("total_amount")
        )
        .values("total")[:1]
    )

    cash_subquery = (
        Payment.objects
        .filter(
            shift_id=OuterRef("pk"),
            method="CASH",
        )
        .values("shift_id")
        .annotate(
            total=Sum("amount")
        )
        .values("total")[:1]
    )

    pos_subquery = (
        Payment.objects
        .filter(
            shift_id=OuterRef("pk"),
            method="POS",
        )
        .values("shift_id")
        .annotate(
            total=Sum("amount")
        )
        .values("total")[:1]
    )

    transfer_subquery = (
        Payment.objects
        .filter(
            shift_id=OuterRef("pk"),
            method="TRANSFER",
        )
        .values("shift_id")
        .annotate(
            total=Sum("amount")
        )
        .values("total")[:1]
    )

    shifts = (
        Shift.objects
        .select_related(
            "user",
            "department",
            "department__hotel",
        )
        .annotate(
            total_sales=Coalesce(
                Subquery(
                    sales_subquery,
                    output_field=decimal_field,
                ),
                Value(0, output_field=decimal_field),
            ),
            cash_total=Coalesce(
                Subquery(
                    cash_subquery,
                    output_field=decimal_field,
                ),
                Value(0, output_field=decimal_field),
            ),
            pos_total=Coalesce(
                Subquery(
                    pos_subquery,
                    output_field=decimal_field,
                ),
                Value(0, output_field=decimal_field),
            ),
            transfer_total=Coalesce(
                Subquery(
                    transfer_subquery,
                    output_field=decimal_field,
                ),
                Value(0, output_field=decimal_field),
            ),
        )
    )

    if user.role == "RESTAURANT":
        shifts = shifts.filter(user=user)

    elif user.role in {"MANAGER", "DIRECTOR", "ADMIN"}:
        hotels = get_accessible_hotels(user)
        shifts = shifts.filter(
            department__hotel__in=hotels
        )

    else:
        shifts = Shift.objects.none()

    shifts = shifts.order_by("-opened_at")

    return render(
        request,
        "restaurant/shift_list.html",
        {
            "shifts": shifts,
        },
    )

@role_required("RESTAURANT", "MANAGER", "DIRECTOR", "ADMIN")
def shift_detail(request, shift_id):
    user = request.user

    shift = get_object_or_404(
        Shift.objects.select_related(
            "user",
            "department",
            "department__hotel",
        ),
        id=shift_id,
    )

    if user.role == "RESTAURANT":
        if shift.user_id != user.id:
            raise PermissionDenied("You cannot access this shift.")

    elif user.role in {"MANAGER", "DIRECTOR", "ADMIN"}:
        hotels = get_accessible_hotels(user)

        if not hotels.filter(id=shift.department.hotel_id).exists():
            raise PermissionDenied("You cannot access this shift.")

    else:
        raise PermissionDenied("You cannot access this shift.")

    orders = (
        shift.orders
        .select_related("created_by", "folio")
        .prefetch_related("items__menu_item")
        .order_by("-created_at")
    )

    payments = (
        shift.payments
        .select_related("account", "folio", "collected_by")
        .order_by("-collected_at")
    )

    handovers = (
        shift.cash_handovers
        .select_related("from_user", "to_user")
        .order_by("-created_at")
    )

    sales_orders = orders.filter(
        status__in=["CHARGED", "PAID"]
    )

    total_sales = (
        sales_orders.aggregate(
            total=Sum("total_amount")
        )["total"]
        or Decimal("0.00")
    )

    payment_summary = {
        "CASH": Decimal("0.00"),
        "POS": Decimal("0.00"),
        "TRANSFER": Decimal("0.00"),
    }

    for payment in payments:
        if payment.method in payment_summary:
            payment_summary[payment.method] += payment.amount

    expected_cash = (
        shift.opening_cash
        + payment_summary["CASH"]
    )

    return render(
        request,
        "restaurant/shift_detail.html",
        {
            "shift": shift,
            "orders": orders,
            "payments": payments,
            "handovers": handovers,
            "total_sales": total_sales,
            "payment_summary": payment_summary,
            "expected_cash": expected_cash,
        },
    )